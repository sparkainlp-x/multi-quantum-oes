"""Deterministic, read-only OES replay analysis and the separate OES32 residual check."""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
from statistics import median
from typing import Any, Sequence

from .contracts import BLOCKS, BLOCK_SIZE, CHANNELS, Frame, ValidationError, load_prereg_bytes, load_replay_bytes


def score_block(values: Sequence[float]) -> float:
    """The fixed score: .45 peak absolute + .35 RMS + .20 mean absolute."""
    if len(values) != BLOCK_SIZE:
        raise ValueError(f"block must contain exactly {BLOCK_SIZE} values")
    xs = [abs(float(v)) for v in values]
    if any(not math.isfinite(v) for v in xs):
        raise ValueError("block values must be finite")
    peak = max(xs)
    if peak == 0.0:
        return 0.0
    scaled = [x / peak for x in xs]
    rms_ratio = math.sqrt(math.fsum(x * x for x in scaled) / BLOCK_SIZE)
    mean_ratio = math.fsum(scaled) / BLOCK_SIZE
    return peak * (0.45 + 0.35 * rms_ratio + 0.20 * mean_ratio)


def block_scores(channels: Sequence[float]) -> tuple[float, ...]:
    if len(channels) != CHANNELS:
        raise ValueError(f"frame must contain exactly {CHANNELS} channels")
    return tuple(score_block(channels[i:i + BLOCK_SIZE]) for i in range(0, CHANNELS, BLOCK_SIZE))


def _divide(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _exposure(indices: list[int], frames: list[Frame], nominal_step: float | None) -> float:
    if not indices or nominal_step is None:
        return 0.0
    segments: list[list[int]] = []
    for i in indices:
        if not segments or i != segments[-1][-1] + 1:
            segments.append([i])
        else:
            segments[-1].append(i)
    total = 0.0
    for seg in segments:
        span = (frames[seg[-1]].timestamp - frames[seg[0]].timestamp).total_seconds()
        total += span + nominal_step
    return total


def detector_metrics(frames: list[Frame], alarms: list[bool], block_flags: list[tuple[bool, ...] | None]) -> dict[str, Any]:
    intervals = [(frames[i].timestamp - frames[i - 1].timestamp).total_seconds() for i in range(1, len(frames))]
    step = median(intervals) if intervals else None
    regimes: dict[str, Any] = {}
    for regime in dict.fromkeys(f.regime for f in frames):
        indices = [i for i, f in enumerate(frames) if f.regime == regime]
        events: dict[str, list[int]] = {}
        for i in indices:
            if frames[i].event_id is not None:
                events.setdefault(frames[i].event_id or "", []).append(i)
        latencies: dict[str, float | None] = {}
        for event_id, event_indices in events.items():
            hit = next((i for i in event_indices if alarms[i]), None)
            latencies[event_id] = None if hit is None else (frames[hit].timestamp - frames[event_indices[0]].timestamp).total_seconds() * 1000
        no_event = [i for i in indices if frames[i].event_id is None]
        false_indices = [i for i in no_event if alarms[i]]
        false_episodes = 0
        previous: int | None = None
        for i in no_event:
            if alarms[i] and previous != i - 1:
                false_episodes += 1
            previous = i if alarms[i] else None
        tp = fp = fn = annotated = 0
        for i in indices:
            truth, pred = frames[i].affected_blocks, block_flags[i]
            if truth is None or pred is None:
                continue
            annotated += 1
            expected = set(truth)
            actual = {b for b, flag in enumerate(pred) if flag}
            tp += len(expected & actual)
            fp += len(actual - expected)
            fn += len(expected - actual)
        exposure = _exposure(indices, frames, step)
        iou_denom = tp + fp + fn
        detected_latencies = [value for value in latencies.values() if value is not None]
        regimes[regime] = {
            "frame_count": len(indices),
            "event_count": len(events),
            "detected_event_count": sum(value is not None for value in latencies.values()),
            "event_recall": _divide(sum(value is not None for value in latencies.values()), len(events)),
            "missed_event_ids": [key for key, value in latencies.items() if value is None],
            "alarm_frame_count": sum(alarms[i] for i in indices),
            "no_event_frame_count": len(no_event),
            "false_positive_frame_count": len(false_indices),
            "false_positive_frame_rate": _divide(len(false_indices), len(no_event)),
            "false_positive_alarm_episode_count": false_episodes,
            "observed_exposure_seconds": exposure,
            "false_positive_frames_per_10min": len(false_indices) * 600.0 / exposure if exposure else None,
            "false_positive_alarm_episodes_per_10min": false_episodes * 600.0 / exposure if exposure else None,
            "block_localization": {
                "annotated_frame_count": annotated,
                "tp_blocks_across_frames": tp,
                "fp_blocks_across_frames": fp,
                "fn_blocks_across_frames": fn,
                "precision": _divide(tp, tp + fp),
                "recall": _divide(tp, tp + fn),
                "iou": _divide(tp, iou_denom),
            },
            "detection_latency_ms": {
                "by_event_id": latencies,
                "mean_detected_event_latency_ms": (sum(detected_latencies) / len(detected_latencies)) if detected_latencies else None,
                "median_detected_event_latency_ms": median(detected_latencies) if detected_latencies else None,
            },
        }
    return regimes


def _candidate_outputs(frames: list[Frame], threshold: float) -> tuple[list[tuple[bool, ...]], list[dict[str, Any]]]:
    flags: list[tuple[bool, ...]] = []
    evidence: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        scores = block_scores(frame.channels)
        row_flags = tuple(score >= threshold for score in scores)
        flags.append(row_flags)
        ranking = sorted(range(BLOCKS), key=lambda b: (-scores[b], b))
        ranked_blocks = [{
            "block": b,
            "channel_range": [b * BLOCK_SIZE, (b + 1) * BLOCK_SIZE],
            "score": scores[b],
            "flagged": row_flags[b],
            "margin_over_threshold": scores[b] - threshold,
        } for b in ranking]
        evidence.append({
            "frame_index": index,
            "timestamp": _iso(frame.timestamp),
            "regime": frame.regime,
            "event_label": frame.event_label,
            "event_id": frame.event_id,
            "alarm": any(row_flags),
            "flagged_blocks": [b for b, flag in enumerate(row_flags) if flag],
            "top_ranked_blocks": ranked_blocks[:5],
            "block_scores": list(scores),
            "rationale": "Each block score is 0.45×peak_abs + 0.35×RMS + 0.20×mean_abs; flagged iff score >= the preregistered threshold.",
        })
    return flags, evidence


def _baseline_outputs(frames: list[Frame], threshold: float) -> tuple[list[tuple[bool, ...]], list[dict[str, Any]]]:
    flags: list[tuple[bool, ...]] = []
    for frame in frames:
        row = tuple(max(abs(v) for v in frame.channels[b * BLOCK_SIZE:(b + 1) * BLOCK_SIZE]) >= threshold for b in range(BLOCKS))
        flags.append(row)
    return flags, [{"frame_index": i, "alarm": any(row), "flagged_blocks": [b for b, value in enumerate(row) if value]} for i, row in enumerate(flags)]


def evaluate_replay(replay_bytes: bytes, prereg_bytes: bytes) -> dict[str, Any]:
    """Evaluate one exact replay/protocol byte pair. No calibration or threshold fitting occurs."""
    frames = load_replay_bytes(replay_bytes)
    protocol = load_prereg_bytes(prereg_bytes)
    candidate_threshold = float(protocol["candidate_threshold"])
    baseline_threshold = float(protocol["baseline_max_abs_threshold"])
    candidate_flags, evidence = _candidate_outputs(frames, candidate_threshold)
    candidate_alarms = [any(row) for row in candidate_flags]
    baseline_flags, _ = _baseline_outputs(frames, baseline_threshold)
    baseline_alarms = [any(row) for row in baseline_flags]
    has_incumbent = any(f.incumbent_flags is not None or f.incumbent_alarm is not None for f in frames)
    incumbent_flags: list[tuple[bool, ...] | None] = [f.incumbent_flags for f in frames] if has_incumbent else []
    incumbent_alarms = [
        f.incumbent_alarm if f.incumbent_alarm is not None else any(f.incumbent_flags or ())
        for f in frames
    ] if has_incumbent else []
    return {
        "report_schema_version": 1,
        "scope": "offline replay triage/evaluation only; not an operational alarm, safety instrument, or process-control function",
        "status": "synthetic_demo" if all(f.regime.startswith("synthetic") for f in frames) else "caller_supplied_replay",
        "protocol": {
            "protocol_id": protocol["protocol_id"],
            "locked_at": protocol["locked_at"],
            "sha256": hashlib.sha256(prereg_bytes).hexdigest(),
            "candidate_threshold": candidate_threshold,
            "baseline_max_abs_threshold": baseline_threshold,
            "candidate_threshold_source": "required preregistration; no tuning",
            "threshold_calibration_status": "uncalibrated example threshold; not a field recommendation",
            "score": {
                "formula": "0.45 * peak(abs(block)) + 0.35 * RMS(block) + 0.20 * mean(abs(block))",
                "channels": CHANNELS,
                "block_size": BLOCK_SIZE,
                "block_count": BLOCKS,
                "comparison": ">=",
            },
            "baseline": "per-block max(abs(channel)) >= baseline_max_abs_threshold",
        },
        "input": {
            "sha256": hashlib.sha256(replay_bytes).hexdigest(),
            "frame_count": len(frames),
            "regimes_in_first_seen_order": list(dict.fromkeys(f.regime for f in frames)),
            "incumbent_available": has_incumbent,
        },
        "detectors": {
            "candidate_oes512_block_score": {
                "regimes": detector_metrics(frames, candidate_alarms, list(candidate_flags)),
            },
            "simple_max_abs_per_block_baseline": {
                "regimes": detector_metrics(frames, baseline_alarms, list(baseline_flags)),
            },
            "incumbent": {
                "regimes": detector_metrics(frames, incumbent_alarms, incumbent_flags),
            } if has_incumbent else None,
        },
        "ranked_frame_evidence": evidence,
        "decision_separation": {
            "operational_replay_path": "deterministic classical score only",
            "quantum_lab_path": "isolated toy simulation; not imported, called, or used to rank or flag replay frames",
        },
    }


def compare_oes32_residual(observed: Any, reference: Any, tolerance: Any) -> dict[str, Any]:
    """Separate 32-value max-absolute residual contract; equality with tolerance passes."""
    def validate_vector(values: Any, name: str) -> list[float]:
        if not isinstance(values, list) or len(values) != 32:
            raise ValidationError(f"{name} must be an array of exactly 32 finite numbers")
        result = []
        for index, value in enumerate(values):
            result.append(_finite(value, f"{name}[{index}]"))
        return result
    obs = validate_vector(observed, "observed")
    ref = validate_vector(reference, "reference")
    tol = _finite(tolerance, "tolerance")
    if tol < 0:
        raise ValidationError("tolerance must be non-negative")
    residuals = [abs(a - b) for a, b in zip(obs, ref)]
    maximum = max(residuals)
    return {
        "contract": "OES32-residual",
        "scope": "separate 32-value pairwise maximum-absolute comparison; not the OES-512 weighted block score",
        "maximum_absolute_residual": maximum,
        "tolerance": tol,
        "comparison": "fail iff residual > tolerance; equality passes",
        "passed": maximum <= tol,
        "largest_residual_index": residuals.index(maximum),
    }


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{field} must be a finite number")
    return number
