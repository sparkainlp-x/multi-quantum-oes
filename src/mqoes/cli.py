"""CLI and dependency-free local preview server."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from typing import Any

from .contracts import ValidationError, _json
from .intersection import render_summary as render_intersection_summary, run_intersection
from .quantum_lab import run_quantum_lab
from .stream32 import simulate_stream32
from .triage import compare_oes32_residual, evaluate_replay
from failure_capsules import validate_capsule
from outage_radar import InputError, analyze as analyze_outage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPLAY = ROOT / "data" / "synthetic_replay.jsonl"
DEFAULT_PREREG = ROOT / "prereg" / "example_locked.json"
DEFAULT_REPORT = ROOT / "reports" / "synthetic-report.json"
DEFAULT_OUTAGE = ROOT / "data" / "outage_snapshot_synthetic.json"
DEFAULT_CAPSULE = ROOT / "data" / "example_failure_capsule.json"
DEFAULT_STREAM32 = ROOT / "data" / "oes32_stream_synthetic.json"
DEFAULT_INTERSECTION = ROOT / "reports" / "intersection.json"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
LOOPBACK_HOST = "127.0.0.1"
REQUEST_TIMEOUT_SECONDS = 15.0


def render_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_json(value), encoding="utf-8")


def _document(raw: bytes, where: str) -> Any:
    try:
        return _json(raw.decode("utf-8"), where)
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{where} must be UTF-8 JSON") from exc


def _residual_file(raw: bytes) -> dict[str, Any]:
    payload = _document(raw, "residual input")
    if not isinstance(payload, dict) or set(payload) != {"observed", "reference", "tolerance"}:
        raise ValidationError("residual input must contain exactly observed, reference, and tolerance")
    return compare_oes32_residual(payload["observed"], payload["reference"], payload["tolerance"])


def _outage_file(raw: bytes) -> dict[str, Any]:
    try:
        return analyze_outage(_document(raw, "outage snapshot"))
    except InputError as exc:
        raise ValidationError(str(exc)) from exc


def _capsule_file(raw: bytes) -> dict[str, Any]:
    errors = validate_capsule(_document(raw, "failure capsule"))
    return {"valid": not errors, "errors": errors, "scope": "metadata format validation only; not certified anonymization or a privacy guarantee"}


def _stream32_file(raw: bytes) -> dict[str, Any]:
    return simulate_stream32(_document(raw, "OES32 stream input"))


def _make_server(host: str, port: int, request_timeout: float = REQUEST_TIMEOUT_SECONDS) -> ThreadingHTTPServer:
    """Build (but do not start) the loopback-only preview server.

    Loopback is enforced here as well as in main(), so programmatic callers
    cannot bind a public interface. Pass port 0 to get an ephemeral port.
    """
    if host != LOOPBACK_HOST:
        raise ValidationError("the prototype web interface is loopback-only; use 127.0.0.1")
    if not 0 <= port <= 65535:
        raise ValidationError("port must be from 0 through 65535")
    web_root = ROOT / "web"
    prereg_bytes = DEFAULT_PREREG.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "MultiQuantumOES/0.1"
        # Socket timeout (seconds) applied by StreamRequestHandler.setup(); bounds
        # request-line, header, and body reads so a stalled client cannot pin a thread.
        timeout = request_timeout

        def _host_allowed(self) -> int | None:
            """Return None if the Host header is acceptable, else an HTTP status."""
            values = self.headers.get_all("Host") or []
            if len(values) != 1 or not values[0].strip():
                return 400
            bound_port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{bound_port}", f"localhost:{bound_port}"}
            return None if values[0].strip().lower() in allowed else 403

        def _reject_host(self) -> bool:
            status = self._host_allowed()
            if status is None:
                return False
            message = b'{"error":"missing or invalid Host header"}\n' if status == 400 else b'{"error":"Host header not allowed; use 127.0.0.1:<port> or localhost:<port>"}\n'
            self.close_connection = True
            self._send(status, message)
            return True

        def _internal_error(self, exc: BaseException) -> None:
            # Log only the exception type locally; never echo exception text to clients.
            self.log_error("internal error handling %s: %s", self.path, type(exc).__name__)
            self._send(500, b'{"error":"internal server error"}\n')

        def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise ValidationError("invalid Content-Length") from exc
            if length < 0 or length > MAX_UPLOAD_BYTES:
                raise ValidationError("request body missing or exceeds 20 MiB limit")
            body = self.rfile.read(length)
            if len(body) != length:
                self.close_connection = True
                raise ValidationError("request body shorter than Content-Length")
            return body

        def do_GET(self) -> None:  # noqa: N802
            if self._reject_host():
                return
            try:
                if self.path in {"/", "/index.html"}:
                    self._send(200, (web_root / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif self.path == "/api/health":
                    self._send(200, b'{"ok":true,"mode":"offline-local-prototype"}\n')
                elif self.path == "/api/demo":
                    self._send(200, render_json(evaluate_replay(DEFAULT_REPLAY.read_bytes(), prereg_bytes)).encode("utf-8"))
                elif self.path == "/api/quantum":
                    self._send(200, render_json(run_quantum_lab()).encode("utf-8"))
                elif self.path == "/api/outage-demo":
                    self._send(200, render_json(_outage_file(DEFAULT_OUTAGE.read_bytes())).encode("utf-8"))
                elif self.path == "/api/capsule-demo":
                    self._send(200, render_json(_capsule_file(DEFAULT_CAPSULE.read_bytes())).encode("utf-8"))
                elif self.path == "/api/stream32-demo":
                    self._send(200, render_json(_stream32_file(DEFAULT_STREAM32.read_bytes())).encode("utf-8"))
                else:
                    self._send(404, b'{"error":"not found"}\n')
            except Exception as exc:
                self._internal_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            if self._reject_host():
                return
            try:
                raw = self._body()
                if self.path == "/api/evaluate":
                    result = evaluate_replay(raw, prereg_bytes)
                elif self.path == "/api/residual":
                    result = _residual_file(raw)
                elif self.path == "/api/outage":
                    result = _outage_file(raw)
                elif self.path == "/api/capsule":
                    result = _capsule_file(raw)
                elif self.path == "/api/stream32":
                    result = _stream32_file(raw)
                else:
                    self._send(404, b'{"error":"not found"}\n')
                    return
                self._send(200, render_json(result).encode("utf-8"))
            except TimeoutError:
                self.close_connection = True
                self._send(408, b'{"error":"request body not received before timeout"}\n')
            except ValidationError as exc:
                # Validation messages are produced by this project's schema checks.
                self._send(400, render_json({"error": str(exc)}).encode("utf-8"))
            except (ValueError, OverflowError):
                self._send(400, b'{"error":"invalid input"}\n')
            except Exception as exc:
                self._internal_error(exc)

        def log_message(self, fmt: str, *args: Any) -> None:
            # Request lines only; uploaded bodies are never logged or stored.
            super().log_message(fmt, *args)

    return ThreadingHTTPServer((host, port), Handler)


def _server(host: str, port: int) -> int:
    server = _make_server(host, port)
    host, port = server.server_address[0], server.server_address[1]
    print(f"Offline prototype listening at http://{host}:{port} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-Quantum OES offline research prototype")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="evaluate the checked-in SYNTHETIC OES replay")
    demo.add_argument("--output", default=str(DEFAULT_REPORT))
    evaluate = commands.add_parser("evaluate", help="evaluate an OES-512 JSONL replay")
    evaluate.add_argument("--input", required=True)
    evaluate.add_argument("--prereg", default=str(DEFAULT_PREREG))
    evaluate.add_argument("--output", default="-")
    residual = commands.add_parser("residual", help="run the separate OES32 residual pair check")
    residual.add_argument("--input", required=True, help="JSON with observed, reference (32 values each), and tolerance")
    outage = commands.add_parser("outage", help="analyze an offline multi-site service-check snapshot")
    outage.add_argument("--input", default=str(DEFAULT_OUTAGE))
    outage.add_argument("--output", default="-")
    capsule = commands.add_parser("capsule", help="validate a metadata-only failure capsule")
    capsule.add_argument("--input", default=str(DEFAULT_CAPSULE))
    stream = commands.add_parser("stream32", help="simulate the separate OES32 adaptive-tau packet router")
    stream.add_argument("--input", default=str(DEFAULT_STREAM32))
    stream.add_argument("--output", default="-")
    quantum = commands.add_parser("quantum", help="run isolated toy quantum research demos")
    quantum.add_argument("--output", default="-")
    intersection = commands.add_parser("intersection", help="AI ∩ quantum: one statevector core, four toy applications")
    intersection.add_argument("--output", default=str(DEFAULT_INTERSECTION), help="JSON report path, or - for stdout only")
    serve = commands.add_parser("serve", help="launch the dependency-free local web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8877)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            report = evaluate_replay(DEFAULT_REPLAY.read_bytes(), DEFAULT_PREREG.read_bytes())
            _write_json(args.output, report)
            print(f"Wrote deterministic synthetic report: {args.output}")
            return 0
        if args.command == "evaluate":
            report = evaluate_replay(Path(args.input).read_bytes(), Path(args.prereg).read_bytes())
            if args.output == "-":
                sys.stdout.write(render_json(report))
            else:
                _write_json(args.output, report)
            return 0
        if args.command == "residual":
            sys.stdout.write(render_json(_residual_file(Path(args.input).read_bytes())))
            return 0
        if args.command == "outage":
            result = _outage_file(Path(args.input).read_bytes())
            if args.output == "-":
                sys.stdout.write(render_json(result))
            else:
                _write_json(args.output, result)
            return 0
        if args.command == "capsule":
            result = _capsule_file(Path(args.input).read_bytes())
            sys.stdout.write(render_json(result))
            return 0 if result["valid"] else 1
        if args.command == "stream32":
            result = _stream32_file(Path(args.input).read_bytes())
            if args.output == "-":
                sys.stdout.write(render_json(result))
            else:
                _write_json(args.output, result)
            return 0
        if args.command == "quantum":
            result = run_quantum_lab()
            if args.output == "-":
                sys.stdout.write(render_json(result))
            else:
                _write_json(args.output, result)
            return 0
        if args.command == "intersection":
            result = run_intersection()
            sys.stdout.write(render_intersection_summary(result))
            if args.output != "-":
                _write_json(args.output, result)
                print(f"Wrote {args.output}")
            return 0
        if args.command == "serve":
            if args.host != "127.0.0.1":
                raise ValidationError("the prototype web interface is loopback-only; use 127.0.0.1")
            if not 1 <= args.port <= 65535:
                raise ValidationError("port must be from 1 through 65535")
            return _server(args.host, args.port)
    except (OSError, ValidationError, ValueError, OverflowError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
