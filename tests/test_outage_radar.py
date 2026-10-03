import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import outage_radar  # noqa: E402


class OutageRadarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "examples" / "sample_input.json").open(encoding="utf-8") as handle:
            cls.sample = json.load(handle)

    def signal(self, result, service):
        return next(item for item in result["signals"] if item["service"] == service)

    def test_example_demonstrates_requested_classifications(self):
        result = outage_radar.analyze(self.sample)
        expected = {
            "catalog": "site-local/connectivity-path",
            "frontend": "no observed failure",
            "inventory": "insufficient evidence/unknown",
            "invoicing": "dependency-wide",
            "payments": "dependency-wide",
            "search": "likely service-wide",
        }
        actual = {item["service"]: item["classification"] for item in result["signals"]}
        self.assertEqual(actual, expected)
        self.assertTrue(set(expected.values()).issuperset(outage_radar.CLASSIFICATIONS - {"no observed failure"}))

    def test_missing_and_unknown_probes_do_not_count_toward_quorum(self):
        signal = self.signal(outage_radar.analyze(self.sample), "inventory")
        evidence = signal["evidence"]
        self.assertEqual(signal["classification"], "insufficient evidence/unknown")
        self.assertEqual(evidence["usable_sites_count"], 1)
        self.assertFalse(evidence["quorum_met"])
        self.assertEqual(evidence["failed_sites"], ["north"])
        self.assertEqual(evidence["unknown_sites"], ["south"])
        self.assertEqual(evidence["missing_sites"], ["east", "west"])

    def test_dependency_wide_requires_service_and_dependency_failures_to_overlap(self):
        document = copy.deepcopy(self.sample)
        for check in document["dependency_checks"]:
            if check["site"] == "west":
                check["status"] = "ok"
        invoicing = self.signal(outage_radar.analyze(document), "invoicing")
        self.assertEqual(invoicing["classification"], "likely service-wide")
        ledger = invoicing["evidence"]["dependencies"][0]
        self.assertEqual(ledger["co_failing_service_sites"], ["east", "north"])

    def test_subquorum_multisite_failure_is_unknown_not_service_wide(self):
        document = {
            "schema_version": 1,
            "observed_at": "2026-09-30T09:15:00Z",
            "sites": ["a", "b", "c"],
            "minimum_quorum_sites": 3,
            "services": [{"id": "api", "dependencies": []}],
            "service_checks": [
                {"site": "a", "service": "api", "status": "fail"},
                {"site": "b", "service": "api", "status": "fail"},
                {"site": "c", "service": "api", "status": "ok"},
            ],
            "dependency_checks": [],
        }
        signal = outage_radar.analyze(document)["signals"][0]
        self.assertEqual(signal["classification"], "insufficient evidence/unknown")
        self.assertTrue(signal["evidence"]["quorum_met"])

    def test_dependency_wide_requires_a_declared_dependency(self):
        document = copy.deepcopy(self.sample)
        document["services"] = [
            service for service in document["services"] if service["id"] != "payments"
        ]
        document["service_checks"] = [
            check for check in document["service_checks"] if check["service"] != "payments"
        ]
        # Keep the dependency declared by invoicing; the matched dependency evidence is still explicit.
        signal = self.signal(outage_radar.analyze(document), "invoicing")
        self.assertEqual(signal["classification"], "dependency-wide")

    def test_json_rendering_is_deterministic_and_independent_of_input_order(self):
        document = copy.deepcopy(self.sample)
        expected = outage_radar._render_json(outage_radar.analyze(document))
        self.assertEqual(expected, outage_radar._render_json(outage_radar.analyze(document)))
        document["sites"].reverse()
        document["services"].reverse()
        document["service_checks"].reverse()
        document["dependency_checks"].reverse()
        self.assertEqual(expected, outage_radar._render_json(outage_radar.analyze(document)))

    def test_schema_rejects_malformed_documents(self):
        malformed_documents = []

        missing_key = copy.deepcopy(self.sample)
        del missing_key["dependency_checks"]
        malformed_documents.append(missing_key)

        unknown_site = copy.deepcopy(self.sample)
        unknown_site["service_checks"][0]["site"] = "not-configured"
        malformed_documents.append(unknown_site)

        duplicate_probe = copy.deepcopy(self.sample)
        duplicate_probe["service_checks"].append(copy.deepcopy(duplicate_probe["service_checks"][0]))
        malformed_documents.append(duplicate_probe)

        invalid_status = copy.deepcopy(self.sample)
        invalid_status["service_checks"][0]["status"] = {"status": "ok"}
        malformed_documents.append(invalid_status)

        invalid_timestamp = copy.deepcopy(self.sample)
        invalid_timestamp["observed_at"] = "yesterday"
        malformed_documents.append(invalid_timestamp)

        invalid_quorum = copy.deepcopy(self.sample)
        invalid_quorum["minimum_quorum_sites"] = True
        malformed_documents.append(invalid_quorum)

        for document in malformed_documents:
            with self.subTest(document=document):
                with self.assertRaises(outage_radar.InputError):
                    outage_radar.analyze(document)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(outage_radar.InputError, "duplicate JSON object key"):
            json.loads('{"schema_version":1,"schema_version":1}', object_pairs_hook=outage_radar._unique_object)

    def test_cli_rejects_malformed_json_without_writing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.json"
            source.write_text('{"schema_version":', encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = outage_radar.main(["--input", str(source)])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invalid JSON", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
