#!/usr/bin/env python3
"""Choose detector thresholds on the CALIBRATION split only, then write a locked prereg.

Rule (fixed before any stress-set evaluation):
  * Use only no-event frames (event_id is null) of data/stress_calibration.jsonl, pooled
    across all regimes.
  * For each detector, a frame's statistic is the maximum over its 16 blocks of the
    detector's block value (candidate: fixed block score; baseline: max |x| in block).
    A frame alarms if that statistic >= threshold.
  * Target no-event frame false-positive rate alpha = 0.05. With N no-event frames,
    allow k = floor(alpha * N) alarms. Let m be the (k+1)-th largest frame statistic.
    The threshold is ceil(m * 1e4) / 1e4, bumped by 1e-4 if that equals m exactly, so the
    (k+1)-th frame cannot alarm. It is rounded to 4 decimals for a human-readable prereg.
  * The held-out data/stress_replay.jsonl is never read by this script.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mqoes.contracts import BLOCK_SIZE, BLOCKS, load_replay_bytes  # noqa: E402
from mqoes.triage import block_scores  # noqa: E402

ALPHA = 0.05
CALIBRATION_PATH = ROOT / "data" / "stress_calibration.jsonl"
TEMPLATE_PREREG = ROOT / "prereg" / "stress_locked.json"
OUTPUT_PREREG = ROOT / "prereg" / "stress_calibrated_locked.json"
OUTPUT_RECORD = ROOT / "reports" / "stress-calibration.json"
LOCKED_AT = "2026-10-02T22:07:00-04:00"


def _threshold(stats: list[float], alpha: float) -> tuple[float, int, float]:
    ordered = sorted(stats, reverse=True)
    k = math.floor(alpha * len(ordered))
    m = ordered[k]
    t = math.ceil(m * 1e4) / 1e4
    if t <= m:
        t = round(t + 1e-4, 4)
    return t, k, m


def calibrate(raw: bytes) -> dict:
    frames = load_replay_bytes(raw)
    no_event = [f for f in frames if f.event_id is None]
    cand = [max(block_scores(f.channels)) for f in no_event]
    base = [max(max(abs(v) for v in f.channels[b * BLOCK_SIZE:(b + 1) * BLOCK_SIZE]) for b in range(BLOCKS)) for f in no_event]
    ct, k, cm = _threshold(cand, ALPHA)
    bt, _, bm = _threshold(base, ALPHA)
    return {
        "calibration_input": {"path": "data/stress_calibration.jsonl", "sha256": hashlib.sha256(raw).hexdigest(),
                              "frame_count": len(frames), "no_event_frame_count": len(no_event)},
        "rule": "per detector: smallest 4-decimal threshold with pooled no-event frame FP rate <= alpha on the calibration split",
        "alpha": ALPHA,
        "allowed_false_positive_frames": k,
        "candidate": {"kth_plus_one_largest_statistic": cm, "threshold": ct,
                      "calibration_fp_frames": sum(s >= ct for s in cand)},
        "baseline": {"kth_plus_one_largest_statistic": bm, "threshold": bt,
                     "calibration_fp_frames": sum(s >= bt for s in base)},
        "held_out_split_read": False,
    }


def main() -> int:
    raw = CALIBRATION_PATH.read_bytes()
    record = calibrate(raw)
    prereg = json.loads(TEMPLATE_PREREG.read_text(encoding="utf-8"))
    prereg["protocol_id"] = "oes512-stress-calibrated-fpr0.05-v1"
    prereg["locked_at"] = LOCKED_AT
    prereg["candidate_threshold"] = record["candidate"]["threshold"]
    prereg["baseline_max_abs_threshold"] = record["baseline"]["threshold"]
    OUTPUT_PREREG.write_text(json.dumps(prereg, indent=2) + "\n", encoding="utf-8")
    record["output_prereg"] = {"path": "prereg/stress_calibrated_locked.json",
                               "sha256": hashlib.sha256(OUTPUT_PREREG.read_bytes()).hexdigest()}
    OUTPUT_RECORD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
