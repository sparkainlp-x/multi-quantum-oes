#!/usr/bin/env python3
"""Generate invented OES-512 *stress* replays (held-out test split + calibration split).

Stdlib only. Nothing is copied from real telemetry. The goal is to probe where the
fixed block score (0.45*peak + 0.35*RMS + 0.20*mean, >=) differs from the per-block
max-abs baseline. Some regimes are expected to favor each detector; see PARAMETERS
and docs/STRESS_RESULTS.md. Parameters were fixed before any evaluation was run.

Usage:
    python3 tools/generate_stress.py            # writes both splits
    python3 tools/generate_stress.py --check    # verify files on disk match the generator
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = 512
BLOCK_SIZE = 32
BLOCKS = 16

STRESS_SEED = 20261003       # held-out stress/test split
CALIBRATION_SEED = 20261004  # separate calibration split (thresholds may be chosen here only)
STRESS_PATH = ROOT / "data" / "stress_replay.jsonl"
CALIBRATION_PATH = ROOT / "data" / "stress_calibration.jsonl"

# All generative parameters, documented and fixed. Amplitudes are in the same
# arbitrary units as the bundled synthetic replay (example thresholds are 0.50).
PARAMETERS = {
    "base_noise_sigma": 0.03,  # Gaussian background noise on every channel
    "regimes": [
        {"name": "stress_stable_control", "frames": 40,
         "description": "Gaussian background only; no events."},
        {"name": "stress_heavy_tail_noise", "frames": 60,
         "description": "Student-t (df=2) background scaled by 0.03 on every channel; plus, with "
                        "probability 0.30 per frame, one isolated channel spike of |0.60-1.20|. No events."},
        {"name": "stress_spike_artifacts", "frames": 40,
         "description": "Gaussian background; with probability 0.80 per frame, one single-channel "
                        "artifact spike of |0.60-1.50| in a random block. Labeled event_label='none'."},
        {"name": "stress_weak_multichannel_burst", "frames": 60,
         "event_label": "weak_multichannel_burst", "episodes": 4, "episode_frames": 3,
         "description": "Each episode: 1-2 random blocks; all 32 channels get a constant offset "
                        "sign*U(0.30, 0.46) for 3 frames (single-channel peaks mostly < 0.50 but RMS/mean elevated)."},
        {"name": "stress_sustained_drift", "frames": 60,
         "event_label": "sustained_drift", "episodes": 3, "episode_frames": 8,
         "description": "Each episode: 1-3 random blocks; all 32 channels ramp linearly to "
                        "sign*U(0.35, 0.60) over 8 frames (offset = A*(k+1)/8), with per-channel jitter N(0, 0.02)."},
        {"name": "stress_sparse_spike_event", "frames": 60,
         "event_label": "sparse_spike_event", "episodes": 4, "episode_frames": 3,
         "description": "Each episode: 1 random block; 3 random channels get sign*U(0.70, 1.00) for 3 frames "
                        "(a real but sparse event; expected to favor the max-abs baseline)."},
    ],
    "episode_layout": "episodes start at frame offsets 8, 8+g, 8+2g, ... within the regime, where g = "
                      "(regime_frames - 16) // episodes; event frames are contiguous and never touch regime edges.",
    "timestamps": "1 second apart, starting 2026-10-03T00:00:00Z (test) / 2026-10-04T00:00:00Z (calibration)",
}


def _student_t(rng: random.Random, df: int) -> float:
    z = rng.gauss(0.0, 1.0)
    chi2 = sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(df))
    return z / math.sqrt(chi2 / df)


def _sign(rng: random.Random) -> float:
    return 1.0 if rng.random() < 0.5 else -1.0


def generate(seed: int, start: datetime) -> list[dict[str, object]]:
    rng = random.Random(seed)
    sigma = PARAMETERS["base_noise_sigma"]
    frames: list[dict[str, object]] = []
    t = 0
    for spec in PARAMETERS["regimes"]:
        name, count = spec["name"], spec["frames"]
        episodes: dict[int, tuple[str, int, list[int], dict[str, object]]] = {}
        if "episodes" in spec:
            gap = (count - 16) // spec["episodes"]
            for e in range(spec["episodes"]):
                first = 8 + e * gap
                event_id = f"{name}-{seed}-{e + 1:02d}"
                if name == "stress_weak_multichannel_burst":
                    blocks = sorted(rng.sample(range(BLOCKS), rng.randint(1, 2)))
                    params = {"offset": _sign(rng) * rng.uniform(0.30, 0.46)}
                elif name == "stress_sustained_drift":
                    blocks = sorted(rng.sample(range(BLOCKS), rng.randint(1, 3)))
                    params = {"amplitude": _sign(rng) * rng.uniform(0.35, 0.60)}
                else:  # sparse spike event
                    blocks = [rng.randrange(BLOCKS)]
                    chans = sorted(rng.sample(range(BLOCK_SIZE), 3))
                    params = {"channels": chans, "values": [_sign(rng) * rng.uniform(0.70, 1.00) for _ in chans]}
                for k in range(spec["episode_frames"]):
                    episodes[first + k] = (event_id, k, blocks, params)
        for i in range(count):
            if name == "stress_heavy_tail_noise":
                values = [sigma * _student_t(rng, 2) for _ in range(CHANNELS)]
                if rng.random() < 0.30:
                    values[rng.randrange(CHANNELS)] += _sign(rng) * rng.uniform(0.60, 1.20)
            else:
                values = [rng.gauss(0.0, sigma) for _ in range(CHANNELS)]
            if name == "stress_spike_artifacts" and rng.random() < 0.80:
                values[rng.randrange(CHANNELS)] += _sign(rng) * rng.uniform(0.60, 1.50)
            label, event_id, affected = "none", None, []
            if i in episodes:
                event_id, k, blocks, params = episodes[i]
                label, affected = spec["event_label"], list(blocks)
                for b in blocks:
                    base = b * BLOCK_SIZE
                    if name == "stress_weak_multichannel_burst":
                        for j in range(base, base + BLOCK_SIZE):
                            values[j] += params["offset"]
                    elif name == "stress_sustained_drift":
                        level = params["amplitude"] * (k + 1) / spec["episode_frames"]
                        for j in range(base, base + BLOCK_SIZE):
                            values[j] += level + rng.gauss(0.0, 0.02)
                    else:
                        for c, v in zip(params["channels"], params["values"]):
                            values[base + c] += v
            stamp = (start + timedelta(seconds=t)).isoformat(timespec="seconds").replace("+00:00", "Z")
            t += 1
            frames.append({
                "timestamp": stamp,
                "channels": [round(v, 8) for v in values],
                "regime": name,
                "event_label": label,
                "event_id": event_id,
                "affected_blocks": affected,
            })
    return frames


def render(frames: list[dict[str, object]]) -> bytes:
    return "".join(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n" for row in frames).encode("utf-8")


def stress_bytes() -> bytes:
    return render(generate(STRESS_SEED, datetime(2026, 10, 3, tzinfo=timezone.utc)))


def calibration_bytes() -> bytes:
    return render(generate(CALIBRATION_SEED, datetime(2026, 10, 4, tzinfo=timezone.utc)))


def main(argv: list[str]) -> int:
    outputs = ((STRESS_PATH, stress_bytes()), (CALIBRATION_PATH, calibration_bytes()))
    if "--check" in argv:
        ok = all(path.exists() and path.read_bytes() == data for path, data in outputs)
        print("stress files match generator" if ok else "stress files DIFFER from generator")
        return 0 if ok else 1
    for path, data in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"Wrote {data.count(b'\n')} frames to {path} sha256={hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
