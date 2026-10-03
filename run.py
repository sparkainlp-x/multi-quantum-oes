#!/usr/bin/env python3
"""ADDED DURING RECONSTRUCTION (2026-10-02) -- NOT PART OF THE ORIGINAL SHARE.

run.py was missing from the Quick Share download. This minimal shim puts the
project root (for top-level failure_capsules.py / outage_radar.py) and src/
(for the mqoes package) on sys.path and delegates to mqoes.cli.main.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from mqoes.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
