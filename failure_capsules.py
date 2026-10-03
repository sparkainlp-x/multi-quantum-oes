"""Offline, privacy-conscious incident capsules with synthetic replay only.

This prototype validates a compact JSON schema and generates deterministic
synthetic sequences. Replay values are invented; validation reads only the
supplied capsule JSON, not device or incident storage.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
GENERATOR_NAME = "synthetic-linear-drift-v1"
MAX_POINTS = 10_000
MAX_TEXT_LENGTH = 2_000

_TOP_LEVEL_KEYS = {
    "schema_version",
    "software",
    "configuration",
    "timestamp_policy",
    "device_channel_group",
    "event_class",
    "coarse_features",
    "replay",
    "behavior",
    "reproducibility_checklist",
}
_CHECKLIST_KEYS = {
    "software_version_recorded",
    "configuration_version_recorded",
    "synthetic_recipe_and_seed_recorded",
    "expected_and_observed_behavior_written",
    "independent_replay_completed",
}
_FORBIDDEN_KEY_NAMES = {
    "raw",
    "rawdata",
    "rawmeasurement",
    "rawmeasurements",
    "rawsensordata",
    "measurement",
    "measurements",
    "sensordata",
    "sensorvalues",
    "samples",
    "samplevalues",
    "waveform",
    "waveforms",
    "timeseries",
    "sensorreadings",
    "readings",
    "traces",
    "signaldata",
    "recordings",
    "payload",
    "values",
}


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _find_forbidden_keys(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            normalized = _normalized_key(key)
            if normalized in _FORBIDDEN_KEY_NAMES or normalized.startswith("raw"):
                found.append(f"{child_path}: forbidden raw-measurement/data key")
            found.extend(_find_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_forbidden_keys(child, f"{path}[{index}]"))
    return found


def _check_object(value: Any, expected: set[str], path: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return False
    missing = expected - value.keys()
    extra = value.keys() - expected
    for key in sorted(missing):
        errors.append(f"{path}.{key} is required")
    for key in sorted(extra, key=str):
        errors.append(f"{path}.{key} is not allowed by schema")
    return not missing and not extra


def _check_text(value: Any, path: str, errors: list[str], limit: int = 120) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
    elif len(value) > limit:
        errors.append(f"{path} must be at most {limit} characters")


def _is_finite_number(value: int | float) -> bool:
    # math.isfinite() raises OverflowError for ints too large for a float
    # (e.g. a 400-digit JSON integer); treat those as non-finite input.
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _check_number(value: Any, path: str, errors: list[str]) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be a number")
        return False
    if not _is_finite_number(value):
        errors.append(f"{path} must be finite")
        return False
    return True


def validate_capsule(capsule: Any) -> list[str]:
    """Return validation errors; an empty list means the capsule is valid."""
    if not isinstance(capsule, dict):
        return ["$: capsule must be a JSON object"]

    errors = _find_forbidden_keys(capsule)
    if not _check_object(capsule, _TOP_LEVEL_KEYS, "$", errors):
        return errors

    if capsule["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")

    software = capsule["software"]
    if _check_object(software, {"name", "version"}, "software", errors):
        _check_text(software["name"], "software.name", errors, 120)
        _check_text(software["version"], "software.version", errors, 80)

    configuration = capsule["configuration"]
    if _check_object(configuration, {"version"}, "configuration", errors):
        _check_text(configuration["version"], "configuration.version", errors, 80)

    timestamp = capsule["timestamp_policy"]
    if _check_object(timestamp, {"policy", "value"}, "timestamp_policy", errors):
        policy, value = timestamp["policy"], timestamp["value"]
        if policy == "omitted":
            if value is not None:
                errors.append("timestamp_policy.value must be null when policy is 'omitted'")
        elif policy == "relative_offset_seconds":
            if _check_number(value, "timestamp_policy.value", errors) and abs(value) > 1e12:
                errors.append("timestamp_policy.value is outside the supported range")
        elif isinstance(policy, str) and policy in {"exact_utc", "rounded_minute_utc", "rounded_hour_utc"}:
            if not isinstance(value, str):
                errors.append("timestamp_policy.value must be an ISO 8601 UTC string")
            else:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                        errors.append("timestamp_policy.value must include a UTC timezone")
                    elif policy == "rounded_minute_utc" and (parsed.second or parsed.microsecond):
                        errors.append("rounded_minute_utc timestamps must have zero seconds and microseconds")
                    elif policy == "rounded_hour_utc" and (parsed.minute or parsed.second or parsed.microsecond):
                        errors.append("rounded_hour_utc timestamps must be at the start of an hour")
                except ValueError:
                    errors.append("timestamp_policy.value must be a valid ISO 8601 timestamp")
        else:
            errors.append(
                "timestamp_policy.policy must be exact_utc, rounded_minute_utc, "
                "rounded_hour_utc, relative_offset_seconds, or omitted"
            )

    categories = capsule["device_channel_group"]
    if _check_object(
        categories,
        {"device_category", "channel_group_category"},
        "device_channel_group",
        errors,
    ):
        _check_text(categories["device_category"], "device_channel_group.device_category", errors)
        _check_text(
            categories["channel_group_category"],
            "device_channel_group.channel_group_category",
            errors,
        )

    _check_text(capsule["event_class"], "event_class", errors)

    features = capsule["coarse_features"]
    if not isinstance(features, dict) or not features:
        errors.append("coarse_features must be a non-empty object of categorical strings")
    else:
        for key, value in features.items():
            if not isinstance(key, str) or not key.strip():
                errors.append("coarse_features keys must be non-empty strings")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"coarse_features.{key} must be a non-empty categorical string")
            elif len(value) > 80:
                errors.append(f"coarse_features.{key} must be at most 80 characters")

    replay = capsule["replay"]
    if _check_object(replay, {"generator", "seed", "recipe"}, "replay", errors):
        if replay["generator"] != GENERATOR_NAME:
            errors.append(f"replay.generator must be {GENERATOR_NAME!r}")
        seed = replay["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            errors.append("replay.seed must be an integer from 0 through 4294967295")
        recipe = replay["recipe"]
        recipe_keys = {"points", "baseline", "noise_stddev", "drift_start", "drift_per_step"}
        if _check_object(recipe, recipe_keys, "replay.recipe", errors):
            points = recipe["points"]
            if isinstance(points, bool) or not isinstance(points, int) or not 1 <= points <= MAX_POINTS:
                errors.append(f"replay.recipe.points must be an integer from 1 through {MAX_POINTS}")
            for key in ("baseline", "noise_stddev", "drift_per_step"):
                _check_number(recipe[key], f"replay.recipe.{key}", errors)
            if _check_number(recipe["noise_stddev"], "replay.recipe.noise_stddev", []):
                if recipe["noise_stddev"] < 0:
                    errors.append("replay.recipe.noise_stddev must not be negative")
            drift_start = recipe["drift_start"]
            if (
                isinstance(drift_start, bool)
                or not isinstance(drift_start, int)
                or not isinstance(points, int)
                or isinstance(points, bool)
                or not 0 <= drift_start <= points
            ):
                errors.append("replay.recipe.drift_start must be an integer from 0 through points")

    behavior = capsule["behavior"]
    if _check_object(behavior, {"expected", "observed"}, "behavior", errors):
        _check_text(behavior["expected"], "behavior.expected", errors, MAX_TEXT_LENGTH)
        _check_text(behavior["observed"], "behavior.observed", errors, MAX_TEXT_LENGTH)

    checklist = capsule["reproducibility_checklist"]
    if _check_object(checklist, _CHECKLIST_KEYS, "reproducibility_checklist", errors):
        for key, value in checklist.items():
            if not isinstance(value, bool):
                errors.append(f"reproducibility_checklist.{key} must be a boolean")

    return errors


def generate_replay(
    seed: int,
    points: int = 32,
    baseline: float = 100.0,
    noise_stddev: float = 0.25,
    drift_start: int = 20,
    drift_per_step: float = 1.0,
) -> list[float]:
    """Generate a deterministic synthetic baseline-plus-drift sequence.

    All returned values are invented by this function; they are not intended
    to represent or contain captured device readings.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer from 0 through 4294967295")
    if isinstance(points, bool) or not isinstance(points, int) or not 1 <= points <= MAX_POINTS:
        raise ValueError(f"points must be an integer from 1 through {MAX_POINTS}")
    if isinstance(drift_start, bool) or not isinstance(drift_start, int) or not 0 <= drift_start <= points:
        raise ValueError("drift_start must be an integer from 0 through points")
    for name, value in (("baseline", baseline), ("noise_stddev", noise_stddev), ("drift_per_step", drift_per_step)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not _is_finite_number(value):
            raise ValueError(f"{name} must be a finite number")
    if noise_stddev < 0:
        raise ValueError("noise_stddev must not be negative")

    rng = random.Random(seed)
    series = []
    for index in range(points):
        drift = max(0, index - drift_start) * drift_per_step
        series.append(round(baseline + rng.gauss(0.0, noise_stddev) + drift, 6))
    return series


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _demo(example_path: Path) -> int:
    try:
        capsule = _load_json(example_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not load example capsule: {exc}", file=sys.stderr)
        return 2
    errors = validate_capsule(capsule)
    if errors:
        print("Example capsule is invalid:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    recipe = capsule["replay"]["recipe"]
    sequence = generate_replay(capsule["replay"]["seed"], **recipe)
    print(json.dumps({
        "capsule_valid": True,
        "generator": capsule["replay"]["generator"],
        "seed": capsule["replay"]["seed"],
        "synthetic_point_count": len(sequence),
        "synthetic_preview_only": sequence[:8],
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate offline failure capsules or run a synthetic demo.")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="validate one capsule JSON file")
    validate_parser.add_argument("path", type=Path)
    commands.add_parser("demo", help="validate the example capsule and print synthetic replay output")
    args = parser.parse_args(argv)

    if args.command == "demo":
        return _demo(Path(__file__).resolve().parent / "examples" / "example_capsule.json")
    try:
        capsule = _load_json(args.path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read capsule: {exc}", file=sys.stderr)
        return 2
    errors = validate_capsule(capsule)
    if errors:
        print("Invalid capsule:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid capsule: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
