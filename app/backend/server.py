from __future__ import annotations

import argparse
import json
import mimetypes
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config_store import ConfigStore
from .models import JobRequest
from .runtime_manager import RuntimeManager


class BackendService:
    def __init__(self) -> None:
        self.config_store = ConfigStore()
        self.runtime_manager = RuntimeManager(self.config_store)


SERVICE = BackendService()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "app" / "frontend"


class BackendHandler(BaseHTTPRequestHandler):
    server_version = "CrayotterBackend/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        raw_path = parsed.path or "/"
        path = raw_path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        try:
            if raw_path == "/ui/":
                self._serve_static(FRONTEND_DIR / "index.html")
                return

            if path == "/ui":
                self._redirect("/ui/")
                return

            if raw_path.startswith("/ui/"):
                relative = raw_path.removeprefix("/ui/")
                self._serve_static(FRONTEND_DIR / relative)
                return

            if path == "/":
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "service": "crayotter-backend",
                        "version": "0.1",
                        "ui": "/ui/",
                        "routes": [
                            "GET /health",
                            "GET /config",
                            "PUT /config",
                            "GET /jobs",
                            "POST /jobs",
                            "GET /jobs/{job_id}",
                            "GET /jobs/{job_id}/artifacts",
                            "GET /jobs/{job_id}/events",
                            "GET /jobs/{job_id}/events/stream",
                            "GET /files?path=<absolute-or-project-relative-path>",
                            "POST /jobs/{job_id}/cancel",
                            "DELETE /jobs/{job_id}",
                        ],
                    },
                )
                return

            if path == "/health":
                self._write_json(HTTPStatus.OK, {"ok": True})
                return

            if path == "/config":
                self._write_json(HTTPStatus.OK, SERVICE.config_store.load().model_dump())
                return

            if path == "/jobs":
                self._write_json(HTTPStatus.OK, {"items": SERVICE.runtime_manager.list_jobs()})
                return

            if path == "/files":
                raw_path = query.get("path", [""])[0]
                if not raw_path:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing path query parameter."})
                    return
                self._serve_project_file(raw_path)
                return

            if path.startswith("/jobs/") and path.endswith("/events/stream"):
                job_id = path.split("/")[2]
                after_sequence = int(query.get("after", ["0"])[0] or 0)
                self._stream_events(job_id=job_id, after_sequence=after_sequence)
                return

            if path.startswith("/jobs/") and path.endswith("/artifacts"):
                job_id = path.split("/")[2]
                items = SERVICE.runtime_manager.list_job_artifacts(job_id)
                self._write_json(HTTPStatus.OK, {"items": items})
                return

            if path.startswith("/jobs/") and path.endswith("/events"):
                job_id = path.split("/")[2]
                after_sequence = int(query.get("after", ["0"])[0] or 0)
                items = SERVICE.runtime_manager.list_events(job_id, after_sequence=after_sequence)
                self._write_json(HTTPStatus.OK, {"items": items})
                return

            if path.startswith("/jobs/"):
                job_id = path.split("/")[2]
                self._write_json(HTTPStatus.OK, SERVICE.runtime_manager.get_job_detail(job_id))
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {path}"})
        except KeyError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Not found: {exc.args[0]}"})
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path != "/config":
                self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {path}"})
                return
            payload = self._read_json()
            config = SERVICE.config_store.update(payload)
            self._write_json(HTTPStatus.OK, config.model_dump())
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path == "/jobs":
                payload = self._read_json()
                request = JobRequest.model_validate(payload)
                record = SERVICE.runtime_manager.create_job(request)
                self._write_json(HTTPStatus.CREATED, record)
                return

            if path.startswith("/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[2]
                result = SERVICE.runtime_manager.cancel_job(job_id)
                self._write_json(HTTPStatus.OK, result)
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {path}"})
        except RuntimeError as exc:
            self._write_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except KeyError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Not found: {exc.args[0]}"})
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path.startswith("/jobs/"):
                job_id = path.split("/")[2]
                result = SERVICE.runtime_manager.delete_job(job_id)
                self._write_json(HTTPStatus.OK, result)
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {path}"})
        except RuntimeError as exc:
            self._write_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except KeyError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Not found: {exc.args[0]}"})
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, socket.error):
            return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def _serve_static(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Static file not found: {path.name}"})
            return
        if not self._is_within_root(resolved, FRONTEND_DIR):
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "Forbidden static path."})
            return
        self._send_file(resolved)

    def _serve_project_file(self, raw_path: str) -> None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve(strict=False)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"File not found: {raw_path}"})
            return

        if not self._is_within_root(resolved, PROJECT_ROOT):
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "Requested file is outside the project workspace."})
            return
        self._send_file(resolved)

    def _send_file(self, path: Path) -> None:
        data = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        if content_type and (
            content_type.startswith("text/")
            or content_type in {"application/json", "application/javascript"}
        ):
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _is_within_root(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _stream_events(self, job_id: str, after_sequence: int = 0) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        cursor = after_sequence
        try:
            while True:
                events = SERVICE.runtime_manager.wait_for_events(job_id, after_sequence=cursor, timeout=1.0)
                if events:
                    for event in events:
                        payload = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        cursor = max(cursor, int(event["sequence"]))
                else:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()

                job = SERVICE.runtime_manager.get_job(job_id)
                if job is None:
                    break
                if job.record.status in {"completed", "failed", "cancelled"} and not events:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    break
        except (ConnectionError, BrokenPipeError):
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Crayotter backend service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), BackendHandler)
    print(f"Crayotter backend listening on http://{args.host}:{args.port}")
    print(f"Crayotter workbench available at http://{args.host}:{args.port}/ui/")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
