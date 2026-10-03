"""Regression tests for fixes made after reconstruction (see CHANGES.md)."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import failure_capsules  # noqa: E402
from mqoes import cli  # noqa: E402
from mqoes.contracts import ValidationError, load_replay_bytes  # noqa: E402
from mqoes.quantum_lab import circuits_equivalent_up_to_global_phase, run_quantum_lab  # noqa: E402

HUGE_INT = int("9" * 400)  # valid JSON integer, too large for a float


class CapsuleOverflowTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads((ROOT / "data" / "example_failure_capsule.json").read_text(encoding="utf-8"))

    def test_huge_relative_offset_is_a_validation_error_not_a_crash(self):
        capsule = json.loads(json.dumps(self.example))
        capsule["timestamp_policy"] = {"policy": "relative_offset_seconds", "value": HUGE_INT}
        errors = failure_capsules.validate_capsule(capsule)
        self.assertIn("timestamp_policy.value must be finite", errors)

    def test_huge_recipe_numbers_are_validation_errors(self):
        for key in ("baseline", "noise_stddev", "drift_per_step"):
            capsule = json.loads(json.dumps(self.example))
            capsule["replay"]["recipe"][key] = HUGE_INT
            self.assertIn(f"replay.recipe.{key} must be finite", failure_capsules.validate_capsule(capsule), key)

    def test_generate_replay_rejects_huge_int_with_value_error(self):
        with self.assertRaisesRegex(ValueError, "baseline must be a finite number"):
            failure_capsules.generate_replay(1, baseline=HUGE_INT)

    def test_cli_capsule_reports_invalid_without_traceback(self):
        capsule = json.loads(json.dumps(self.example))
        capsule["timestamp_policy"] = {"policy": "relative_offset_seconds", "value": HUGE_INT}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "huge.json"
            path.write_text(json.dumps(capsule), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["capsule", "--input", str(path)])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["valid"])
        self.assertNotIn("Traceback", err.getvalue())


class QuantumEquivalenceTests(unittest.TestCase):
    ORIGINAL = [
        {"gate": "H"}, {"gate": "H"},
        {"gate": "RZ", "angle": 0.4}, {"gate": "RZ", "angle": -0.1},
        {"gate": "X"}, {"gate": "X"}, {"gate": "H"},
    ]

    def test_correct_compilation_is_equivalent(self):
        compiled = [{"gate": "RZ", "angle": 0.30000000000000004}, {"gate": "H"}]
        self.assertTrue(circuits_equivalent_up_to_global_phase(self.ORIGINAL, compiled))
        self.assertTrue(run_quantum_lab()["demos"]["circuit_synthesis_compilation"]["equivalent_up_to_global_phase"])

    def test_dropped_rz_is_not_equivalent(self):
        self.assertFalse(circuits_equivalent_up_to_global_phase(self.ORIGINAL, [{"gate": "H"}]))

    def test_wrong_rz_angle_is_not_equivalent(self):
        self.assertFalse(circuits_equivalent_up_to_global_phase(self.ORIGINAL, [{"gate": "RZ", "angle": 1.0}, {"gate": "H"}]))

    def test_global_phase_only_difference_is_equivalent(self):
        # RZ(2*pi) = -I: a pure global phase.
        self.assertTrue(circuits_equivalent_up_to_global_phase([{"gate": "RZ", "angle": 2 * 3.141592653589793}], []))


class ReplayLineSplittingTests(unittest.TestCase):
    def setUp(self):
        self.first_line = (ROOT / "data" / "synthetic_replay.jsonl").read_text(encoding="utf-8").split("\n")[0]

    def test_unicode_line_separator_inside_json_string_is_accepted(self):
        line = self.first_line.replace('"synthetic_stable"', '"synthetic\u2028stable"')
        frames = load_replay_bytes((line + "\n").encode("utf-8"))
        self.assertEqual(frames[0].regime, "synthetic\u2028stable")

    def test_crlf_line_endings_are_accepted(self):
        lf = (ROOT / "data" / "synthetic_replay.jsonl").read_bytes()
        self.assertEqual(load_replay_bytes(lf.replace(b"\n", b"\r\n")), load_replay_bytes(lf))

    def test_blank_lines_still_rejected(self):
        with self.assertRaisesRegex(ValidationError, "blank lines"):
            load_replay_bytes((self.first_line + "\n\n").encode("utf-8"))


class ServerHardeningTests(unittest.TestCase):
    def setUp(self):
        # Keep request logs from the server thread out of the test output.
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def _start(self, timeout: float = 5.0):
        server = cli._make_server("127.0.0.1", 0, request_timeout=timeout)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def _request(self, port: int, method: str, path: str, host: str | None, body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest(method, path, skip_host=True)
        if host is not None:
            conn.putheader("Host", host)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        return response.status, response.read()

    def test_make_server_refuses_non_loopback(self):
        for host in ("0.0.0.0", "localhost", "::1", "192.0.2.1"):
            with self.assertRaises(ValidationError, msg=host):
                cli._make_server(host, 0)

    def test_allowed_host_headers(self):
        port = self._start()
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}"):
            status, body = self._request(port, "GET", "/api/health", host)
            self.assertEqual(status, 200, host)
            self.assertIn(b'"ok":true', body)

    def test_foreign_or_wrong_port_host_is_forbidden(self):
        port = self._start()
        for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}"):
            status, _ = self._request(port, "GET", "/api/health", host)
            self.assertEqual(status, 403, host)
        status, _ = self._request(port, "POST", "/api/residual", "rebind.example", b"{}")
        self.assertEqual(status, 403)

    def test_missing_host_is_bad_request(self):
        port = self._start()
        status, _ = self._request(port, "GET", "/api/health", None)
        self.assertEqual(status, 400)

    def test_stalled_body_times_out(self):
        port = self._start(timeout=0.5)
        with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
            sock.sendall(f"POST /api/residual HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 100\r\n\r\n{{".encode())
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.0 408") or data.startswith(b"HTTP/1.1 408"), data[:80])

    def test_internal_errors_do_not_echo_exception_text(self):
        port = self._start()
        secret = ROOT / "data" / "does-not-exist-secret-path.json"
        with mock.patch.object(cli, "DEFAULT_OUTAGE", secret):
            status, body = self._request(port, "GET", "/api/outage-demo", f"127.0.0.1:{port}")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body), {"error": "internal server error"})
        self.assertNotIn(b"secret", body)


class ImportPathTests(unittest.TestCase):
    def test_suites_run_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("test_prototype.py", "test_stream32.py"):
                proc = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-p", name],
                    cwd=directory, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
