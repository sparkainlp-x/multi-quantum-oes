"""Strict, offline validation for the OES-512 replay contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any

CHANNELS = 512
BLOCK_SIZE = 32
BLOCKS = 16
SCORE_SPEC = {
    "channel_count": CHANNELS,
    "block_size": BLOCK_SIZE,
    "weights": {"peak_abs": 0.45, "rms": 0.35, "mean_abs": 0.20},
    "comparison": ">=",
}
FRAME_REQUIRED = {"timestamp", "channels", "regime", "event_label", "event_id"}
FRAME_OPTIONAL = {"affected_blocks", "incumbent_flags", "incumbent_alarm"}
PREREG_KEYS = {
    "schema_version", "protocol_id", "locked_at", "candidate_threshold",
    "baseline_max_abs_threshold", "score_spec", "event_hit_rule",
    "localization_rule", "latency_rule",
}


class ValidationError(ValueError):
    """Input bytes do not satisfy a documented schema."""


@dataclass(frozen=True)
class Frame:
    timestamp: datetime
    channels: tuple[float, ...]
    regime: str
    event_label: str
    event_id: str | None
    affected_blocks: tuple[int, ...] | None
    incumbent_flags: tuple[bool, ...] | None
    incumbent_alarm: bool | None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValidationError(f"non-standard JSON number {value!r} is not allowed")


def _json(text: str, where: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except ValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{where}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a JSON number, not a boolean")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValidationError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{field} must be finite")
    return result


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValidationError(f"{field} must be a non-empty string without outer whitespace")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be an ISO-8601 timestamp with a timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field} must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def _blocks(value: Any, field: str, is_event: bool) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be null or an array of block indices")
    items: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < BLOCKS:
            raise ValidationError(f"{field}[{index}] must be an integer from 0 through 15")
        if item in items:
            raise ValidationError(f"{field} must not contain duplicate block indices")
        items.append(item)
    if is_event and not items:
        raise ValidationError(f"{field} must be non-empty for an event with known localization")
    if not is_event and items:
        raise ValidationError(f"{field} must be empty on a no-event frame")
    return tuple(sorted(items))


def _parse_frame(item: Any, location: str) -> Frame:
    if not isinstance(item, dict):
        raise ValidationError(f"{location} must be a JSON object")
    keys = set(item)
    missing = FRAME_REQUIRED - keys
    extra = keys - FRAME_REQUIRED - FRAME_OPTIONAL
    if missing or extra:
        details = []
        if missing:
            details.append("missing fields: " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown fields: " + ", ".join(sorted(extra)))
        raise ValidationError(f"{location}: " + "; ".join(details))
    ts = _timestamp(item["timestamp"], f"{location}.timestamp")
    regime = _string(item["regime"], f"{location}.regime")
    label = _string(item["event_label"], f"{location}.event_label")
    event_id = item["event_id"]
    if label == "none":
        if event_id is not None:
            raise ValidationError(f"{location}.event_id must be null when event_label is 'none'")
        is_event = False
    else:
        event_id = _string(event_id, f"{location}.event_id")
        is_event = True
    values = item["channels"]
    if not isinstance(values, list) or len(values) != CHANNELS:
        raise ValidationError(f"{location}.channels must contain exactly {CHANNELS} numbers")
    channels = tuple(_number(value, f"{location}.channels[{i}]") for i, value in enumerate(values))
    affected = _blocks(item["affected_blocks"], f"{location}.affected_blocks", is_event) if "affected_blocks" in item else None
    flags = None
    if "incumbent_flags" in item:
        raw_flags = item["incumbent_flags"]
        if not isinstance(raw_flags, list) or len(raw_flags) != BLOCKS or any(type(v) is not bool for v in raw_flags):
            raise ValidationError(f"{location}.incumbent_flags must be an array of 16 booleans")
        flags = tuple(raw_flags)
    alarm = item.get("incumbent_alarm")
    if "incumbent_alarm" in item and type(alarm) is not bool:
        raise ValidationError(f"{location}.incumbent_alarm must be a boolean")
    return Frame(ts, channels, regime, label, event_id, affected, flags, alarm)


def load_replay_bytes(raw: bytes) -> list[Frame]:
    """Validate UTF-8 JSONL and event-contiguity rules; retain exact raw bytes separately."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("replay must be UTF-8") from exc
    if not text:
        raise ValidationError("replay is empty")
    frames: list[Frame] = []
    # Split on LF only (tolerating CRLF). str.splitlines() would also split on
    # U+2028/U+2029/VT/FF etc., which JSON permits unescaped inside strings.
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    for line_number, line in enumerate(lines, 1):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip():
            raise ValidationError(f"line {line_number}: blank lines are not allowed")
        frames.append(_parse_frame(_json(line, f"line {line_number}"), f"line {line_number}"))
    if not frames:
        raise ValidationError("replay contains no frames")
    for index in range(1, len(frames)):
        if frames[index].timestamp <= frames[index - 1].timestamp:
            raise ValidationError(f"timestamps must be strictly increasing (frames {index} and {index + 1})")
    active_id: str | None = None
    seen_ids: set[str] = set()
    identities: dict[str, tuple[str, str]] = {}
    for index, frame in enumerate(frames, 1):
        event_id = frame.event_id
        if event_id is None:
            if active_id is not None:
                seen_ids.add(active_id)
            active_id = None
            continue
        identity = (frame.regime, frame.event_label)
        if event_id in identities and identities[event_id] != identity:
            raise ValidationError(f"event_id {event_id!r} changes regime or event_label at frame {index}")
        identities[event_id] = identity
        if event_id != active_id:
            if event_id in seen_ids:
                raise ValidationError(f"event_id {event_id!r} must occupy one contiguous run")
            if active_id is not None:
                seen_ids.add(active_id)
            active_id = event_id
    has_incumbent = [f.incumbent_flags is not None or f.incumbent_alarm is not None for f in frames]
    if any(has_incumbent) and not all(has_incumbent):
        raise ValidationError("incumbent signals, when present, must cover every replay frame")
    return frames


def _exact_keys(value: Any, expected: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{where} must be an object")
    missing, extra = expected - set(value), set(value) - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing keys: " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown keys: " + ", ".join(sorted(extra)))
        raise ValidationError(f"{where}: " + "; ".join(details))
    return value


def load_prereg_bytes(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("preregistration must be UTF-8") from exc
    protocol = _exact_keys(_json(text, "preregistration"), PREREG_KEYS, "preregistration")
    if type(protocol["schema_version"]) is not int or protocol["schema_version"] != 1:
        raise ValidationError("preregistration.schema_version must equal integer 1")
    _string(protocol["protocol_id"], "preregistration.protocol_id")
    _timestamp(protocol["locked_at"], "preregistration.locked_at")
    for name in ("candidate_threshold", "baseline_max_abs_threshold"):
        if _number(protocol[name], f"preregistration.{name}") < 0:
            raise ValidationError(f"preregistration.{name} must be non-negative")
    score = _exact_keys(protocol["score_spec"], {"channel_count", "block_size", "weights", "comparison"}, "score_spec")
    if type(score["channel_count"]) is not int or score["channel_count"] != CHANNELS:
        raise ValidationError(f"score_spec.channel_count must be {CHANNELS}")
    if type(score["block_size"]) is not int or score["block_size"] != BLOCK_SIZE:
        raise ValidationError(f"score_spec.block_size must be {BLOCK_SIZE}")
    weights = _exact_keys(score["weights"], {"peak_abs", "rms", "mean_abs"}, "score_spec.weights")
    for key, expected in SCORE_SPEC["weights"].items():
        if _number(weights[key], f"score_spec.weights.{key}") != expected:
            raise ValidationError("score_spec weights must match the fixed OES score contract")
    if score["comparison"] != ">=":
        raise ValidationError("score_spec.comparison must be '>='")
    fixed = {
        "event_hit_rule": "any_alert_frame_during_event",
        "localization_rule": "framewise_micro_precision_recall_iou",
        "latency_rule": "first_alert_frame_minus_first_event_frame_ms",
    }
    for key, expected in fixed.items():
        if protocol[key] != expected:
            raise ValidationError(f"preregistration.{key} must be {expected!r}")
    return protocol
