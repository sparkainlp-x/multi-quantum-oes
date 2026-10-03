from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # top-level failure_capsules/outage_radar used by mqoes.cli
from mqoes.contracts import ValidationError
from mqoes.stream32 import simulate_stream32


class Stream32Tests(unittest.TestCase):
    def test_boundary_uses_strict_greater_than_and_shock_overrides(self):
        result = simulate_stream32({
            "tau_sensitivity": 1.0,
            "rate_limit_threshold": 1.0,
            "reset_state": True,
            "packets": [
                {"data_value": 1.0, "is_shock": False},
                {"data_value": 1.0, "is_shock": True},
            ],
        })
        self.assertEqual([row["route"] for row in result["packet_trace"]], ["primary", "residual"])
        self.assertEqual(result["packet_trace"][0]["routing_reason"], "weighted value <= threshold")
        self.assertEqual(result["packet_trace"][1]["routing_reason"], "shock flag")

    def test_adaptive_state_updates_and_clamps(self):
        result = simulate_stream32({
            "tau_sensitivity": 4.0,
            "rate_limit_threshold": 0.0,
            "reset_state": True,
            "packets": [{"data_value": 100.0, "is_shock": False}],
        })
        self.assertEqual(result["primary_stream"], [])
        self.assertEqual(result["adaptive_tau_observations"], [0.05])

    def test_supplied_example_preserves_distinct_streams(self):
        import json
        example = json.loads((ROOT / "data" / "oes32_stream_synthetic.json").read_text())
        result = simulate_stream32(example)
        self.assertEqual(len(result["packet_trace"]), 4)
        self.assertEqual(len(result["primary_stream"]) + len(result["residual_stream"]), 4)
        self.assertEqual(result["scope"], "separate from OES-512 weighted replay scoring and OES32-residual pairwise comparison")

    def test_invalid_packet_rejected(self):
        with self.assertRaises(ValidationError):
            simulate_stream32({"tau_sensitivity": 1, "rate_limit_threshold": 0.5, "reset_state": True, "packets": [{"data_value": True, "is_shock": False}]})


if __name__ == "__main__":
    unittest.main()
