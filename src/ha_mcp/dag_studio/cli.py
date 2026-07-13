# mypy: disable-error-code=no-untyped-def
from __future__ import annotations

import argparse
import ipaddress
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

from ha_mcp.utils.data_paths import get_data_dir

from .models import DagDocument
from .repository import JsonDagRepository, RevisionConflict
from .service import DagStudioService


def create_handler(  # noqa: C901
    service: DagStudioService, max_bytes: int = 1048576
):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, data: object, content_type="application/json"):
            raw = (
                data
                if isinstance(data, bytes)
                else json.dumps(data, default=str).encode()
            )
            self.send_response(status)
            for k, v in {
                "Content-Type": content_type,
                "Content-Length": str(len(raw)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'",
            }.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _json(self):
            n = int(self.headers.get("Content-Length", "0"))
            if n > max_bytes:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n))

        def do_GET(self):
            p = urlparse(self.path).path
            if p in ("/", "/dag-studio", "/dag-studio/"):
                self._send(
                    200,
                    files("ha_mcp.dag_studio.web.static")
                    .joinpath("index.html")
                    .read_bytes(),
                    "text/html; charset=utf-8",
                )
            elif p == "/dag-studio/api/health":
                self._send(200, {"ok": True, "data": {"ai_provider": "disabled"}})
            elif p == "/dag-studio/api/dags":
                self._send(
                    200,
                    {
                        "ok": True,
                        "data": [d.model_dump(mode="json") for d in service.list()],
                    },
                )
            elif p.startswith("/dag-studio/api/dags/"):
                self._send(
                    200,
                    {
                        "ok": True,
                        "data": service.get(p.rsplit("/", 1)[-1]).model_dump(
                            mode="json"
                        ),
                    },
                )
            else:
                self._send(404, {"ok": False, "error": {"code": "NOT_FOUND"}})

        def do_POST(self):
            try:
                p = urlparse(self.path).path
                body = self._json()
                if p == "/dag-studio/api/dags":
                    result = service.create(DagDocument.model_validate(body))
                    self._send(
                        201, {"ok": True, "data": result.model_dump(mode="json")}
                    )
                elif p.endswith("/validate"):
                    self._send(
                        200,
                        {
                            "ok": True,
                            "data": [
                                f.model_dump()
                                for f in service.validate(
                                    DagDocument.model_validate(body)
                                )
                            ],
                        },
                    )
                elif p.endswith("/approve"):
                    result = service.approve(
                        p.split("/")[-2],
                        int(body["revision"]),
                        body.get("approved_by", "local user"),
                    )
                    self._send(
                        200, {"ok": True, "data": result.model_dump(mode="json")}
                    )
                else:
                    self._send(404, {"ok": False, "error": {"code": "NOT_FOUND"}})
            except Exception as e:
                self._send(
                    422,
                    {
                        "ok": False,
                        "error": {"code": type(e).__name__, "message": str(e)},
                    },
                )

        def do_PUT(self):
            try:
                body = self._json()
                result = service.save(
                    DagDocument.model_validate(body), int(body["revision"])
                )
                self._send(200, {"ok": True, "data": result.model_dump(mode="json")})
            except RevisionConflict as e:
                self._send(
                    409,
                    {
                        "ok": False,
                        "error": {"code": "DAG_REVISION_CONFLICT", "message": str(e)},
                    },
                )
            except Exception as e:
                self._send(
                    422,
                    {
                        "ok": False,
                        "error": {"code": type(e).__name__, "message": str(e)},
                    },
                )

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Handler


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--data-dir")
    p.add_argument("--open-browser", action="store_true")
    p.add_argument("--allow-remote", action="store_true")
    a = p.parse_args()
    try:
        loopback = ipaddress.ip_address(a.host).is_loopback
    except ValueError:
        loopback = a.host == "localhost"
    if not loopback and not a.allow_remote:
        p.error("--allow-remote is required for non-loopback hosts")
    root = Path(a.data_dir) if a.data_dir else get_data_dir() / "dag-studio"
    service = DagStudioService(JsonDagRepository(root))
    httpd = ThreadingHTTPServer((a.host, a.port), create_handler(service))
    url = f"http://{a.host}:{httpd.server_port}/dag-studio/"
    print(url, flush=True)
    if a.open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDAG Studio stopped.", flush=True)
    finally:
        httpd.server_close()
