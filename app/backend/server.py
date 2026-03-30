from __future__ import annotations

import argparse
import cgi
import json
import mimetypes
import re
import socket
import shutil
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config_store import ConfigStore
from .models import JobRequest
from .runtime_manager import RuntimeManager
from app.runtime_paths import configure_runtime_environment, get_bundle_root, get_runtime_root, resource_path, runtime_path


class BackendService:
    def __init__(self) -> None:
        self.config_store = ConfigStore()
        self.runtime_manager = RuntimeManager(self.config_store)


SERVICE = BackendService()
configure_runtime_environment()

BUNDLE_ROOT = get_bundle_root()
RUNTIME_ROOT = get_runtime_root()
FRONTEND_DIR = resource_path("app", "frontend")
UPLOADS_DIR = runtime_path("user_temp")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


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
                            "GET /uploads",
                            "POST /uploads",
                            "DELETE /uploads?path=user_temp/<file>",
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

            if path == "/uploads":
                self._write_json(HTTPStatus.OK, {"items": self._list_upload_items()})
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
            if path == "/uploads":
                items = self._handle_upload_request()
                self._write_json(HTTPStatus.CREATED, {"items": items})
                return

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
        query = parse_qs(parsed.query)

        try:
            if path == "/uploads":
                raw_path = query.get("path", [""])[0]
                if not raw_path:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing path query parameter."})
                    return
                removed = self._delete_upload(raw_path)
                self._write_json(HTTPStatus.OK, removed)
                return

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
            candidate = (RUNTIME_ROOT / candidate).resolve(strict=False)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": f"File not found: {raw_path}"})
            return

        if not self._is_allowed_file_path(resolved):
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

    @classmethod
    def _is_allowed_file_path(cls, path: Path) -> bool:
        return cls._is_within_root(path, RUNTIME_ROOT) or cls._is_within_root(path, BUNDLE_ROOT)

    @staticmethod
    def _sanitize_upload_name(filename: str) -> str:
        raw_name = Path(filename or "").name.strip()
        stem = Path(raw_name).stem or "uploaded_video"
        suffix = Path(raw_name).suffix.lower()
        safe_stem = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", stem).strip("_") or "uploaded_video"
        safe_suffix = suffix if re.fullmatch(r"\.[0-9A-Za-z]{1,10}", suffix or "") else ""
        return f"{safe_stem}{safe_suffix}"

    @staticmethod
    def _allocate_upload_path(filename: str) -> Path:
        candidate = UPLOADS_DIR / filename
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while True:
            deduped = UPLOADS_DIR / f"{stem}_{index}{suffix}"
            if not deduped.exists():
                return deduped
            index += 1

    @staticmethod
    def _display_upload_path(path: Path) -> str:
        relative = path.relative_to(UPLOADS_DIR)
        return (Path("user_temp") / relative).as_posix()

    @classmethod
    def _serialize_upload_item(cls, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "name": path.name,
            "path": str(path.resolve()),
            "display_path": cls._display_upload_path(path),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }

    @classmethod
    def _list_upload_items(cls) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(UPLOADS_DIR.rglob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            items.append(cls._serialize_upload_item(path))
        return items

    @classmethod
    def _resolve_upload_path(cls, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (RUNTIME_ROOT / candidate).resolve(strict=False)
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(UPLOADS_DIR.resolve())
        except Exception:
            return None
        return resolved

    def _handle_upload_request(self) -> list[dict[str, Any]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("Upload requests must use multipart/form-data.")

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
            keep_blank_values=False,
        )

        raw_fields = form["files"] if "files" in form else form["file"] if "file" in form else None
        if raw_fields is None:
            raise ValueError("No files were provided.")

        fields = raw_fields if isinstance(raw_fields, list) else [raw_fields]
        uploaded: list[dict[str, Any]] = []
        for field in fields:
            filename = getattr(field, "filename", "") or ""
            file_obj = getattr(field, "file", None)
            if not filename or file_obj is None:
                continue

            target_name = self._sanitize_upload_name(filename)
            target_path = self._allocate_upload_path(target_name)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("wb") as handle:
                shutil.copyfileobj(file_obj, handle)
            uploaded.append(self._serialize_upload_item(target_path))

        if not uploaded:
            raise ValueError("No valid files were uploaded.")

        return uploaded

    def _delete_upload(self, raw_path: str) -> dict[str, Any]:
        resolved = self._resolve_upload_path(raw_path)
        if resolved is None:
            raise ValueError("Upload path is outside user_temp.")
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Upload not found: {raw_path}")
        resolved.unlink()
        return {"deleted": True, "path": str(resolved), "display_path": self._display_upload_path(resolved)}

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


def build_http_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), BackendHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Crayotter backend service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    httpd = build_http_server(host=args.host, port=args.port)
    print(f"Crayotter backend listening on http://{args.host}:{args.port}")
    print(f"Crayotter workbench available at http://{args.host}:{args.port}/ui/")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
