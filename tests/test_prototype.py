from __future__ import annotations

import json
import io
from contextlib import redirect_stderr
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # top-level failure_capsules/outage_radar used by mqoes.cli

from mqoes.contracts import ValidationError, load_prereg_bytes, load_replay_bytes
from mqoes.cli import main
from mqoes.quantum_lab import run_quantum_lab
from mqoes.triage import block_scores, compare_oes32_residual, evaluate_replay, score_block


class ContractsAndScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prereg = (ROOT / "prereg" / "example_locked.json").read_bytes()
        cls.replay = (ROOT / "data" / "synthetic_replay.jsonl").read_bytes()

    def test_fixed_score_and_block_layout(self):
        self.assertAlmostEqual(score_block([0.5] * 32), 0.5)
        scores = block_scores([0.0] * 512)
        self.assertEqual(len(scores), 16)
        self.assertEqual(scores, (0.0,) * 16)

    def test_candidate_uses_greater_or_equal_threshold(self):
        channels = [0.0] * 512
        channels[7 * 32:8 * 32] = [0.5] * 32
        row = {"timestamp": "2026-10-02T00:00:00Z", "channels": channels, "regime": "boundary", "event_label": "none", "event_id": None, "affected_blocks": []}
        raw = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        report = evaluate_replay(raw, self.prereg)
        evidence = report["ranked_frame_evidence"][0]
        self.assertEqual(evidence["flagged_blocks"], [7])
        self.assertAlmostEqual(evidence["block_scores"][7], 0.5)

    def test_threshold_is_required_and_locked_in_preregistration(self):
        protocol = json.loads(self.prereg)
        del protocol["candidate_threshold"]
        with self.assertRaises(ValidationError):
            load_prereg_bytes(json.dumps(protocol).encode())

    def test_validation_rejects_bools_nan_wrong_size_and_duplicate_keys(self):
        base = {"timestamp": "2026-10-02T00:00:00Z", "channels": [0.0] * 512, "regime": "x", "event_label": "none", "event_id": None}
        for bad in (True, float("nan")):
            row = dict(base)
            row["channels"] = list(base["channels"])
            row["channels"][10] = bad
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    load_replay_bytes((json.dumps(row, allow_nan=True) + "\n").encode())
        row = dict(base)
        row["channels"] = [0.0] * 511
        with self.assertRaises(ValidationError):
            load_replay_bytes((json.dumps(row) + "\n").encode())
        with self.assertRaises(ValidationError):
            load_replay_bytes(b'{"timestamp":"x","timestamp":"y"}\n')

    def test_event_must_be_contiguous(self):
        def row(second, event):
            return {"timestamp": f"2026-10-02T00:00:{second:02d}Z", "channels": [0.0] * 512, "regime": "r", "event_label": "burst" if event else "none", "event_id": "e1" if event else None}
        raw = "\n".join(json.dumps(v) for v in [row(0, True), row(1, False), row(2, True)]) + "\n"
        with self.assertRaisesRegex(ValidationError, "contiguous"):
            load_replay_bytes(raw.encode())

    def test_candidate_and_max_abs_comparison_are_independent(self):
        channels = [0.0] * 512
        channels[3] = 0.7
        row = {"timestamp": "2026-10-02T00:00:00Z", "channels": channels, "regime": "comparison", "event_label": "none", "event_id": None}
        report = evaluate_replay((json.dumps(row) + "\n").encode(), self.prereg)
        candidate = report["detectors"]["candidate_oes512_block_score"]["regimes"]["comparison"]
        baseline = report["detectors"]["simple_max_abs_per_block_baseline"]["regimes"]["comparison"]
        self.assertEqual(candidate["alarm_frame_count"], 0)
        self.assertEqual(baseline["alarm_frame_count"], 1)

    def test_ranked_evidence_is_deterministic_and_hash_audited(self):
        first = evaluate_replay(self.replay, self.prereg)
        second = evaluate_replay(self.replay, self.prereg)
        self.assertEqual(first, second)
        self.assertEqual(first["input"]["frame_count"], 80)
        self.assertEqual(len(first["input"]["sha256"]), 64)
        self.assertEqual(first["protocol"]["threshold_calibration_status"], "uncalibrated example threshold; not a field recommendation")
        alerts = [r for r in first["ranked_frame_evidence"] if r["alarm"]]
        self.assertTrue(alerts)
        self.assertTrue(all(len(row["top_ranked_blocks"]) == 5 for row in alerts))
        for row in alerts:
            scores = [item["score"] for item in row["top_ranked_blocks"]]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_synthetic_demo_detects_both_labeled_events(self):
        report = evaluate_replay(self.replay, self.prereg)
        regimes = report["detectors"]["candidate_oes512_block_score"]["regimes"]
        burst = regimes["synthetic_localized_burst"]
        shock = regimes["synthetic_global_shock"]
        self.assertEqual(burst["event_recall"], 1.0)
        self.assertEqual(shock["event_recall"], 1.0)
        self.assertEqual(burst["block_localization"]["iou"], 1.0)
        self.assertEqual(shock["block_localization"]["iou"], 1.0)
        self.assertEqual(report["decision_separation"]["quantum_lab_path"], "isolated toy simulation; not imported, called, or used to rank or flag replay frames")


class ContractSeparationTests(unittest.TestCase):
    def test_web_server_refuses_non_loopback_binding(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = main(["serve", "--host", "0.0.0.0", "--port", "8877"])
        self.assertEqual(code, 2)
        self.assertIn("loopback-only", error.getvalue())

    def test_oes32_residual_equality_passes_strict_greater_fails(self):
        observed, reference = [0.0] * 32, [0.0] * 32
        observed[12] = 0.5
        exact = compare_oes32_residual(observed, reference, 0.5)
        self.assertTrue(exact["passed"])
        self.assertEqual(exact["largest_residual_index"], 12)
        observed[12] = 0.50001
        self.assertFalse(compare_oes32_residual(observed, reference, 0.5)["passed"])

    def test_residual_rejects_wrong_length_and_boolean(self):
        with self.assertRaises(ValidationError):
            compare_oes32_residual([0] * 31, [0] * 32, 0.1)
        bad = [0] * 32
        bad[0] = True
        with self.assertRaises(ValidationError):
            compare_oes32_residual(bad, [0] * 32, 0.1)

    def test_quantum_demos_are_tiny_and_isolated(self):
        result = run_quantum_lab()
        self.assertTrue(result["not_used_for_triage"])
        demos = result["demos"]
        self.assertEqual({"quantum_machine_learning", "quantum_optimization", "circuit_synthesis_compilation", "chemical_ai"}, set(demos))
        self.assertEqual(demos["quantum_optimization"]["exact_exhaustive"]["assignments_evaluated"], 16)
        self.assertTrue(demos["circuit_synthesis_compilation"]["equivalent_up_to_global_phase"])
        self.assertEqual(demos["chemical_ai"]["status"], "not_implemented")
        self.assertEqual(demos["quantum_machine_learning"]["predictions"][0]["predicted_label"], "low")
        self.assertEqual(demos["quantum_machine_learning"]["predictions"][-1]["predicted_label"], "high")


if __name__ == "__main__":
    unittest.main()
