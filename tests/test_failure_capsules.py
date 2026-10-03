import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from failure_capsules import generate_replay, validate_capsule  # noqa: E402


class CapsuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "examples" / "example_capsule.json").open(encoding="utf-8") as handle:
            cls.example = json.load(handle)

    def test_example_capsule_is_valid(self):
        self.assertEqual(validate_capsule(self.example), [])

    def test_replay_is_deterministic_for_same_seed_and_recipe(self):
        recipe = self.example["replay"]["recipe"]
        first = generate_replay(self.example["replay"]["seed"], **recipe)
        second = generate_replay(self.example["replay"]["seed"], **recipe)
        self.assertEqual(first, second)
        self.assertEqual(len(first), recipe["points"])

    def test_replay_changes_with_seed(self):
        self.assertNotEqual(generate_replay(11), generate_replay(12))

    def test_required_fields_are_enforced(self):
        capsule = json.loads(json.dumps(self.example))
        del capsule["event_class"]
        self.assertTrue(any("event_class is required" in error for error in validate_capsule(capsule)))

    def test_forbidden_raw_measurement_keys_are_rejected_recursively(self):
        capsule = json.loads(json.dumps(self.example))
        capsule["coarse_features"]["Raw_Measurements"] = "should-not-be-here"
        capsule["coarse_features"]["raw_samples"] = [1.0, 2.0]
        errors = validate_capsule(capsule)
        self.assertTrue(any("forbidden raw-measurement/data key" in error for error in errors))
        self.assertGreaterEqual(
            sum("forbidden raw-measurement/data key" in error for error in errors), 2
        )

    def test_malformed_schema_and_non_categorical_features_are_rejected(self):
        capsule = json.loads(json.dumps(self.example))
        capsule["timestamp_policy"]["policy"] = "nearest-ish"
        capsule["coarse_features"]["peak_level_bucket"] = 123.4
        errors = validate_capsule(capsule)
        self.assertTrue(any("timestamp_policy.policy must be" in error for error in errors))
        self.assertTrue(any("coarse_features.peak_level_bucket" in error for error in errors))

    def test_non_string_timestamp_policy_is_rejected_without_crashing(self):
        capsule = json.loads(json.dumps(self.example))
        capsule["timestamp_policy"]["policy"] = ["not", "a", "policy"]
        errors = validate_capsule(capsule)
        self.assertTrue(any("timestamp_policy.policy must be" in error for error in errors))

    def test_unknown_schema_fields_are_rejected(self):
        capsule = json.loads(json.dumps(self.example))
        capsule["unreviewed_extension"] = "unexpected"
        self.assertTrue(any("is not allowed by schema" in error for error in validate_capsule(capsule)))

    def test_malformed_json_shape_is_rejected(self):
        self.assertTrue(validate_capsule(["not", "an", "object"]))


if __name__ == "__main__":
    unittest.main()
