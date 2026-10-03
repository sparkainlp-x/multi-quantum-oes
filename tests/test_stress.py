"""Tests for the stress replay, its calibration split, preregistrations, and reports."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from mqoes.cli import render_json  # noqa: E402
from mqoes.contracts import BLOCK_SIZE, BLOCKS, load_prereg_bytes, load_replay_bytes  # noqa: E402
from mqoes.triage import block_scores, evaluate_replay  # noqa: E402


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"_tool_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_tool("generate_stress")
cal = _load_tool("calibrate_stress")
MANIFEST = json.loads((ROOT / "prereg" / "STRESS_LOCK_MANIFEST.json").read_text(encoding="utf-8"))
REPORTS = {
    "prereg/stress_locked.json": "reports/stress-report-fixed-0.50.json",
    "prereg/stress_calibrated_locked.json": "reports/stress-report-calibrated.json",
}


class StressGeneratorTests(unittest.TestCase):
    def test_generator_is_deterministic_and_matches_files(self):
        self.assertEqual(gen.stress_bytes(), gen.stress_bytes())
        self.assertEqual(gen.stress_bytes(), (ROOT / "data" / "stress_replay.jsonl").read_bytes())
        self.assertEqual(gen.calibration_bytes(), (ROOT / "data" / "stress_calibration.jsonl").read_bytes())

    def test_splits_use_distinct_seeds_and_differ(self):
        self.assertNotEqual(gen.STRESS_SEED, gen.CALIBRATION_SEED)
        self.assertNotEqual(gen.stress_bytes(), gen.calibration_bytes())
        self.assertNotIn(gen.STRESS_SEED, (20261002,))  # distinct from the bundled demo replay seed

    def test_files_validate_with_expected_structure(self):
        expected_regimes = [spec["name"] for spec in gen.PARAMETERS["regimes"]]
        for name in ("stress_replay.jsonl", "stress_calibration.jsonl"):
            frames = load_replay_bytes((ROOT / "data" / name).read_bytes())
            self.assertEqual(len(frames), 320, name)
            self.assertEqual(list(dict.fromkeys(f.regime for f in frames)), expected_regimes)
            self.assertEqual(len({f.event_id for f in frames if f.event_id}), 11)
            for f in frames:
                if f.event_id is None:
                    self.assertEqual(f.affected_blocks, ())
                else:
                    self.assertTrue(f.affected_blocks)


class StressPreregistrationTests(unittest.TestCase):
    def test_manifest_hashes_match_files(self):
        # The sha256 map is the pre-evaluation record. Data, preregistrations and reports must
        # still match it exactly. A tool file may differ only through a documented, code-only
        # amendment that chains from the recorded pre-evaluation hash to the current hash.
        amendments = {a["file"]: a for a in MANIFEST.get("amendments", [])}
        for rel, a in amendments.items():
            self.assertTrue(rel.startswith("tools/") and rel.endswith(".py"), f"amendment not allowed for {rel}")
            self.assertEqual(a["kind"], "code-only")
            self.assertEqual(a["pre_evaluation_sha256"], MANIFEST["sha256"][rel])
            self.assertTrue(a["reason"] and a["verification"] and a["date"])
        for rel, digest in MANIFEST["sha256"].items():
            actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            expected = amendments[rel]["current_sha256"] if rel in amendments else digest
            self.assertEqual(actual, expected, rel)

    def test_fixed_prereg_uses_untuned_example_thresholds(self):
        prereg = load_prereg_bytes((ROOT / "prereg" / "stress_locked.json").read_bytes())
        self.assertEqual((prereg["candidate_threshold"], prereg["baseline_max_abs_threshold"]), (0.5, 0.5))

    def test_calibrated_thresholds_reproduce_from_calibration_split_only(self):
        record = cal.calibrate((ROOT / "data" / "stress_calibration.jsonl").read_bytes())
        prereg = load_prereg_bytes((ROOT / "prereg" / "stress_calibrated_locked.json").read_bytes())
        self.assertEqual(prereg["candidate_threshold"], record["candidate"]["threshold"])
        self.assertEqual(prereg["baseline_max_abs_threshold"], record["baseline"]["threshold"])
        # No string literal in the calibration tool's code (docstring excluded) names the held-out split.
        tree = ast.parse((ROOT / "tools" / "calibrate_stress.py").read_text(encoding="utf-8"))
        docstring_node = tree.body[0].value
        literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not docstring_node]
        self.assertFalse([v for v in literals if "stress_replay" in v])
        self.assertEqual(cal.CALIBRATION_PATH.name, "stress_calibration.jsonl")

    def test_reports_record_prereg_and_input_hashes_and_reproduce(self):
        replay = (ROOT / "data" / "stress_replay.jsonl").read_bytes()
        for prereg_rel, report_rel in REPORTS.items():
            prereg = (ROOT / prereg_rel).read_bytes()
            shipped = (ROOT / report_rel).read_bytes()
            report = json.loads(shipped)
            self.assertEqual(report["protocol"]["sha256"], MANIFEST["sha256"][prereg_rel])
            self.assertEqual(report["input"]["sha256"], MANIFEST["sha256"]["data/stress_replay.jsonl"])
            self.assertEqual(render_json(evaluate_replay(replay, prereg)).encode("utf-8"), shipped)


class ScoreBoundTests(unittest.TestCase):
    def test_candidate_flags_are_subset_of_baseline_at_equal_threshold(self):
        # score = .45*peak + .35*RMS + .20*mean <= peak, so at equal thresholds the
        # candidate can never flag a block the max-abs baseline does not.
        for f in load_replay_bytes((ROOT / "data" / "stress_replay.jsonl").read_bytes()):
            scores = block_scores(f.channels)
            for b in range(BLOCKS):
                peak = max(abs(v) for v in f.channels[b * BLOCK_SIZE:(b + 1) * BLOCK_SIZE])
                self.assertLessEqual(scores[b], peak + 1e-12)


if __name__ == "__main__":
    unittest.main()
