"""Serve run-it's live state on localhost so the web dashboard (/environment) can show it while run-it runs.

    GET http://127.0.0.1:47321/state  ->  the latest snapshot (schema "run-it.state/1"), JSON

Only browsers on an allowed origin may read it (CORS allowlist: the hosted site and local dev servers, plus
RUN_IT_ALLOW_ORIGINS), because the process table is private: command lines can carry secrets. Requests whose
Host header is not localhost/127.0.0.1 are refused, so a DNS-rebinding page cannot reach it either.
Nothing is written to disk and nothing leaves the machine; the server stops when run-it exits.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 47321
SCHEMA = "run-it.state/1"
DEFAULT_ORIGINS = (
    "https://main.dz3ac51z9uzpr.amplifyapp.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def allowed_origins() -> frozenset[str]:
    extra = os.environ.get("RUN_IT_ALLOW_ORIGINS", "")
    return frozenset([*DEFAULT_ORIGINS, *(o.strip().rstrip("/") for o in extra.split(",") if o.strip())])


class LiveState:
    """The latest snapshot, replaced whole on every update (readers never see a half-built one)."""

    def __init__(self):
        self._body = json.dumps({"schema": SCHEMA, "status": "starting"}).encode()
        self._lock = threading.Lock()

    def update(self, snapshot: dict):
        body = json.dumps({"schema": SCHEMA, **snapshot}, separators=(",", ":")).encode()
        with self._lock:
            self._body = body

    def body(self) -> bytes:
        with self._lock:
            return self._body


def _handler(state: LiveState, origins: frozenset[str], port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "run-it"
        sys_version = ""

        def _refuse(self, code: int, message: str):
            body = json.dumps({"error": message}).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check(self) -> str | None:
            """The allowed Origin to echo (or "" for same-machine tools like curl), None to refuse."""
            if self.headers.get("Host", "") not in hosts:
                self._refuse(403, "host not allowed")
                return None
            origin = self.headers.get("Origin")
            if origin is None:
                return ""
            if origin not in origins:
                self._refuse(403, "origin not allowed; add it to RUN_IT_ALLOW_ORIGINS")
                return None
            return origin

        def _cors(self, origin: str):
            if origin:
                self.send_header("access-control-allow-origin", origin)
                self.send_header("vary", "Origin")

        def do_OPTIONS(self):  # noqa: N802 (http.server naming)
            origin = self._check()
            if origin is None:
                return
            self.send_response(204)
            self._cors(origin)
            self.send_header("access-control-allow-methods", "GET")
            self.send_header("access-control-max-age", "600")
            # Chrome's Private Network Access preflight for a public page calling localhost.
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("access-control-allow-private-network", "true")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            origin = self._check()
            if origin is None:
                return
            if self.path.split("?", 1)[0] != "/state":
                self._refuse(404, "not found")
                return
            body = state.body()
            self.send_response(200)
            self._cors(origin)
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the terminal dashboard clean
            pass

    return Handler


def serve(state: LiveState, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Start the server on 127.0.0.1 in a daemon thread. Raises OSError if the port is taken."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    # The Host allowlist needs the bound port (port 0 picks a free one, as the tests do).
    server.RequestHandlerClass = _handler(state, allowed_origins(), server.server_address[1])
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="run-it-live", daemon=True).start()
    return server
