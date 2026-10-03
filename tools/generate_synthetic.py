#!/usr/bin/env python3
"""Generate an invented 80-frame OES replay; no supplied telemetry is copied."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = 512
BLOCK_SIZE = 32
SEED = 20261002


def generate() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    start = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
    frames: list[dict[str, object]] = []
    for i in range(80):
        if i < 20:
            regime, sigma = "synthetic_stable", 0.025
        elif i < 40:
            regime, sigma = "synthetic_noisy", 0.12
        elif i < 60:
            regime, sigma = "synthetic_localized_burst", 0.025
        else:
            regime, sigma = "synthetic_global_shock", 0.015
        values = [rng.gauss(0.0, sigma) for _ in range(CHANNELS)]
        label, event_id, affected = "none", None, []
        if 45 <= i < 48:
            label, event_id, affected = "localized_burst", "synthetic-burst-001", [5]
            for j in range(5 * BLOCK_SIZE, 6 * BLOCK_SIZE):
                values[j] += 0.85
        if 67 <= i < 70:
            label, event_id, affected = "global_shock", "synthetic-shock-001", list(range(16))
            values = [v + 0.58 for v in values]
        stamp = (start + timedelta(seconds=i)).isoformat(timespec="seconds").replace("+00:00", "Z")
        frames.append({
            "timestamp": stamp,
            "channels": [round(value, 8) for value in values],
            "regime": regime,
            "event_label": label,
            "event_id": event_id,
            "affected_blocks": affected,
        })
    return frames


def main() -> None:
    target = ROOT / "data" / "synthetic_replay.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in generate())
    target.write_text(payload, encoding="utf-8", newline="\n")
    print(f"Wrote {len(generate())} synthetic frames (seed={SEED}) to {target}")


if __name__ == "__main__":
    main()
