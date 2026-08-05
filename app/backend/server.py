from __future__ import annotations

import argparse
try:
    import cgi
except ImportError:  # Python 3.13+; public trial uploads are disabled anyway.
    cgi = None
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import socket
import shutil
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .config_store import ConfigStore
from .models import JobRequest
from .runtime_manager import RuntimeManager
from app.backend import auth as auth_service
from app.backend import db
from app.backend import model_config as model_config_service
from app.media_index import build_analysis_index, is_video_file, match_analysis_files
from app.runtime_paths import configure_runtime_environment, get_bundle_root, get_runtime_root, resource_path, runtime_path


class BackendService:
    def __init__(self) -> None:
        self.config_store = ConfigStore()
        self.config_store.load()
        self.runtime_manager = RuntimeManager(self.config_store)
        # 初始化数据库连接池；若未配置 DATABASE_URL 则延迟报错，避免无 DB 场景启动失败。
        try:
            db.init_pool()
        except RuntimeError as exc:
            import logging
            logging.getLogger(__name__).warning("Database not initialized: %s", exc)


SERVICE = BackendService()
configure_runtime_environment()

BUNDLE_ROOT = get_bundle_root()
RUNTIME_ROOT = get_runtime_root()
FRONTEND_DIR = resource_path("app", "frontend")
UPLOADS_DIR = runtime_path("user_temp")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_UPLOADS_DIR = runtime_path("public_uploads")

# Session cookie 配置（注意：与现有匿名 owner_id 的 crayotter_session 区分）
SESSION_COOKIE_NAME = "crayotter_auth_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
# 持久登录（remember-me）cookie：selector:validator，DB 仅存 validator digest，使用即轮换
REMEMBER_COOKIE_NAME = "crayotter_remember"
REMEMBER_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
PUBLIC_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
MAX_JSON_BODY_BYTES = int(os.environ.get("CRAYOTTER_MAX_JSON_BODY_BYTES", "65536"))
PUBLIC_UPLOAD_MAX_BYTES = int(os.environ.get("CRAYOTTER_PUBLIC_UPLOAD_MAX_BYTES", str(500 * 1024 * 1024)))
PUBLIC_UPLOAD_SESSION_MAX_BYTES = int(os.environ.get("CRAYOTTER_PUBLIC_UPLOAD_SESSION_MAX_BYTES", str(1500 * 1024 * 1024)))
PUBLIC_JOBS_PER_HOUR = int(os.environ.get("CRAYOTTER_PUBLIC_JOBS_PER_HOUR", "3"))
PUBLIC_ACTIVE_JOBS_PER_SESSION = int(os.environ.get("CRAYOTTER_PUBLIC_ACTIVE_JOBS_PER_SESSION", "1"))


class PublicTrialGuard:
    def __init__(self) -> None:
        self._submissions: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check_submission(self, owner_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [item for item in self._submissions.get(owner_id, []) if item >= now - 3600]
            if len(recent) >= PUBLIC_JOBS_PER_HOUR:
                raise RuntimeError("Trial limit reached. Please try again later.")
            active = sum(item["status"] in {"queued", "running"} for item in SERVICE.runtime_manager.list_jobs(owner_id))
            if active >= PUBLIC_ACTIVE_JOBS_PER_SESSION:
                raise RuntimeError("You already have a queued or running job.")
            recent.append(now)
            self._submissions[owner_id] = recent


PUBLIC_TRIAL_GUARD = PublicTrialGuard()


class BackendHandler(BaseHTTPRequestHandler):
    server_version = "CrayotterBackend/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        raw_path = parsed.path or "/"
        path = raw_path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        owner_id = self._owner_id()

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
                self._redirect("/ui/")
                return

            if path == "/health":
                self._write_json(HTTPStatus.OK, {"ok": True})
                return

            if path == "/api/auth/me":
                user = self._auth_user()
                if user is None:
                    # 无有效 session 时尝试 remember token 自动续期（含轮换与盗窃检测）
                    renewed = self._renew_from_remember()
                    if renewed is None:
                        self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                        return
                    user = renewed["user"]
                    self._write_json(
                        HTTPStatus.OK,
                        {"user": user, "renewed": True},
                        set_auth_token=renewed["session_token"],
                        set_remember_token=renewed["remember_token"],
                    )
                    return
                self._write_json(HTTPStatus.OK, {"user": user})
                return

            if path == "/api/auth/preferences":
                user = self._auth_user()
                if user is None:
                    self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                    return
                self._write_json(HTTPStatus.OK, {"preferences": auth_service.get_preferences(user["id"])})
                return

            if path == "/api/auth/model-config":
                user = self._auth_user()
                if user is None:
                    self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                    return
                self._write_json(HTTPStatus.OK, {"model_config": model_config_service.public_view(user["id"])})
                return

            if path == "/config":
                self._write_json(HTTPStatus.OK, self._public_config())
                return

            if path == "/jobs":
                self._write_json(HTTPStatus.OK, {"items": SERVICE.runtime_manager.list_jobs(owner_id)})
                return

            if path == "/files":
                raw_path = query.get("path", [""])[0]
                if not raw_path:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing path query parameter."})
                    return
                self._serve_project_file_for_owner(
                    raw_path,
                    owner_id,
                    download=query.get("download", ["0"])[0].lower() in {"1", "true", "yes"},
                )
                return

            if path == "/uploads":
                items = self._list_upload_items(self._uploads_root(owner_id))
                self._write_json(HTTPStatus.OK, {"items": self._filter_upload_items(items, query)})
                return

            if path.startswith("/jobs/") and path.endswith("/events/stream"):
                job_id = path.split("/")[2]
                after_sequence = int(query.get("after", ["0"])[0] or 0)
                self._stream_events(job_id=job_id, after_sequence=after_sequence, owner_id=owner_id)
                return

            if path.startswith("/jobs/") and path.endswith("/artifacts"):
                job_id = path.split("/")[2]
                items = SERVICE.runtime_manager.list_job_artifacts(job_id, owner_id)
                self._write_json(HTTPStatus.OK, {"items": items})
                return

            if path.startswith("/jobs/") and path.endswith("/events"):
                job_id = path.split("/")[2]
                after_sequence = int(query.get("after", ["0"])[0] or 0)
                items = SERVICE.runtime_manager.list_events(job_id, after_sequence=after_sequence, owner_id=owner_id)
                self._write_json(HTTPStatus.OK, {"items": items})
                return

            if path.startswith("/jobs/") and path.endswith("/events.log"):
                job_id = path.split("/")[2]
                self._write_text_attachment(
                    HTTPStatus.OK,
                    SERVICE.runtime_manager.events_log_text(job_id, owner_id),
                    filename=f"{job_id}-events.log",
                )
                return

            if path.startswith("/jobs/") and path.endswith("/messages"):
                job_id = path.split("/")[2]
                items = SERVICE.runtime_manager.list_messages(job_id, owner_id)
                self._write_json(HTTPStatus.OK, {"items": items})
                return

            if path.startswith("/jobs/") and "/plans/" in path:
                parts = path.split("/")
                job_id = parts[2]
                if len(parts) >= 5 and parts[3] == "plans" and parts[4] == "current":
                    self._write_json(HTTPStatus.OK, SERVICE.runtime_manager.get_current_plan(job_id, owner_id))
                    return
                if len(parts) >= 5 and parts[3] == "plans" and parts[4] == "diff":
                    from_version = query.get("from", [""])[0]
                    to_version = query.get("to", [""])[0]
                    self._write_json(
                        HTTPStatus.OK,
                        SERVICE.runtime_manager.get_plan_diff(job_id, from_version, to_version, owner_id),
                    )
                    return
                if len(parts) >= 5 and parts[3] == "plans":
                    self._write_json(HTTPStatus.OK, SERVICE.runtime_manager.get_plan(job_id, parts[4], owner_id))
                    return

            if path.startswith("/jobs/"):
                job_id = path.split("/")[2]
                self._write_json(HTTPStatus.OK, SERVICE.runtime_manager.get_job_detail(job_id, owner_id))
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
            if path == "/api/auth/model-config":
                user = self._auth_user()
                if user is None:
                    self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                    return
                payload = self._read_json()
                view = model_config_service.update(user["id"], payload)
                self._write_json(HTTPStatus.OK, {"model_config": view})
                return
            if path != "/config":
                self._write_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {path}"})
                return
            if self._public_mode:
                self._write_json(HTTPStatus.FORBIDDEN, {"error": "Server configuration is managed by the operator."})
                return
            payload = self._read_json()
            config = SERVICE.config_store.update(payload)
            self._write_json(HTTPStatus.OK, config.model_dump())
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        owner_id = self._owner_id()

        try:
            if path == "/api/auth/register":
                payload = self._read_json()
                result = auth_service.register(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    ip_address=self._client_ip(),
                    user_agent=self._client_user_agent(),
                )
                # 注册成功后自动建立会话，前端可直接进入工作台
                session_token, _ = auth_service.create_session(
                    result["user"]["id"],
                    ip_address=self._client_ip(),
                    user_agent=self._client_user_agent(),
                )
                self._write_json(HTTPStatus.CREATED, result, set_auth_token=session_token)
                return

            if path == "/api/auth/login":
                payload = self._read_json()
                result = auth_service.login(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    remember_me=bool(payload.get("remember_me")),
                    ip_address=self._client_ip(),
                    user_agent=self._client_user_agent(),
                )
                self._write_json(
                    HTTPStatus.OK,
                    {"user": result["user"], "expires_at": result["expires_at"]},
                    set_auth_token=result["token"],
                    set_remember_token=result.get("remember_token"),
                )
                return

            if path == "/api/auth/logout":
                token = self._session_token()
                if token:
                    auth_service.logout(token, ip_address=self._client_ip())
                auth_service.revoke_remember_token(self._remember_cookie())
                self._write_json(HTTPStatus.OK, {"ok": True}, clear_auth=True, clear_remember=True)
                return

            if path == "/api/auth/password":
                user = self._auth_user()
                if user is None:
                    self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                    return
                payload = self._read_json()
                auth_service.change_password(
                    user["id"],
                    str(payload.get("old_password") or ""),
                    str(payload.get("new_password") or ""),
                    ip_address=self._client_ip(),
                )
                self._write_json(HTTPStatus.OK, {"ok": True}, clear_auth=True)
                return

            if path == "/api/auth/reset":
                payload = self._read_json()
                auth_service.reset_password_by_recovery_code(
                    str(payload.get("username") or ""),
                    str(payload.get("recovery_code") or ""),
                    str(payload.get("new_password") or ""),
                    ip_address=self._client_ip(),
                )
                self._write_json(HTTPStatus.OK, {"ok": True}, clear_auth=True, clear_remember=True)
                return

            if path == "/api/auth/preferences":
                user = self._auth_user()
                if user is None:
                    self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                    return
                payload = self._read_json()
                merged = auth_service.update_preferences(user["id"], payload.get("preferences") or {})
                self._write_json(HTTPStatus.OK, {"preferences": merged})
                return

            if path == "/uploads":
                items = self._handle_upload_request(self._uploads_root(owner_id), public=self._public_mode)
                self._write_json(HTTPStatus.CREATED, {"items": items})
                return

            if path == "/jobs":
                # 登录用户启用自有 API（BYOK 持久化）时使用其密钥，且不占用平台公开配额；
                # 否则走平台配额（公开限流）或浏览器头部 BYOK（匿名、不落库）。
                user = self._auth_user()
                own_overrides = model_config_service.get_runtime_overrides(user["id"]) if user else {}
                if not own_overrides:
                    self._check_public_submission(owner_id)
                payload = self._read_json()
                request = JobRequest.model_validate(payload)
                if self._public_mode:
                    # Workflow switches are task-scoped and safe to honour.
                    # Do not let an anonymous browser select local browser
                    # profiles, server-side material settings, or a different
                    # persisted model profile.
                    request = JobRequest(
                        task=request.task,
                        mode=request.mode,
                        enable_phase2_research=request.enable_phase2_research,
                        enable_plan_review=request.enable_plan_review,
                        direct_phase3_execution=request.direct_phase3_execution,
                        prefer_local_materials=request.prefer_local_materials,
                        target_duration_seconds=request.target_duration_seconds,
                        deadline_seconds=request.deadline_seconds,
                        processing_mode=request.processing_mode,
                        output_profile=request.output_profile,
                        enabled_material_platforms=request.enabled_material_platforms,
                    )
                record = SERVICE.runtime_manager.create_job(
                    request,
                    owner_id,
                    own_overrides or (self._public_runtime_overrides() if self._public_mode else None),
                    self._uploads_root(owner_id) if self._public_mode else None,
                )
                self._write_json(HTTPStatus.CREATED, record)
                return

            if path.startswith("/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[2]
                result = SERVICE.runtime_manager.cancel_job(job_id, owner_id)
                self._write_json(HTTPStatus.OK, result)
                return

            if path.startswith("/jobs/") and path.endswith("/resume"):
                job_id = path.split("/")[2]
                payload = self._read_json() or {}
                strategy = str(payload.get("strategy") or "resume")
                result = SERVICE.runtime_manager.resume_job(job_id, owner_id, strategy=strategy)
                self._write_json(HTTPStatus.OK, result)
                return

            if path.startswith("/jobs/") and path.endswith("/messages"):
                job_id = path.split("/")[2]
                payload = self._read_json()
                result = SERVICE.runtime_manager.add_message(
                    job_id,
                    str(payload.get("content") or ""),
                    owner_id,
                )
                self._write_json(HTTPStatus.CREATED, result)
                return

            if path.startswith("/jobs/") and path.endswith("/pause"):
                job_id = path.split("/")[2]
                payload = self._read_json()
                result = SERVICE.runtime_manager.pause_job(
                    job_id,
                    str(payload.get("mode") or "next_safe_point"),
                    owner_id,
                )
                self._write_json(HTTPStatus.OK, result)
                return

            if path.startswith("/jobs/") and path.endswith("/approve") and "/plans/" not in path:
                job_id = path.split("/")[2]
                payload = self._read_json()
                result = SERVICE.runtime_manager.approve_job(
                    job_id,
                    str(payload.get("pause_token") or ""),
                    owner_id,
                )
                self._write_json(HTTPStatus.OK, result)
                return

            if path.startswith("/jobs/") and "/plans/" in path:
                parts = path.split("/")
                job_id = parts[2]
                version = parts[4] if len(parts) > 4 else ""
                action = parts[5] if len(parts) > 5 else ""
                payload = self._read_json()
                if action == "feedback":
                    result = SERVICE.runtime_manager.apply_plan_feedback(
                        job_id,
                        version,
                        str(payload.get("feedback") or payload.get("content") or ""),
                        owner_id,
                    )
                    self._write_json(HTTPStatus.CREATED, result)
                    return
                if action == "approve":
                    result = SERVICE.runtime_manager.approve_plan(job_id, version, owner_id)
                    self._write_json(HTTPStatus.OK, result)
                    return
                if action == "reject":
                    result = SERVICE.runtime_manager.reject_plan(job_id, version, owner_id)
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
        owner_id = self._owner_id()

        try:
            if path == "/api/auth/model-config":
                user = self._auth_user()
                if user is None:
                    self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "Not authenticated"})
                    return
                model_config_service.clear(user["id"])
                self._write_json(HTTPStatus.OK, {"ok": True})
                return

            if path == "/uploads":
                raw_path = query.get("path", [""])[0]
                if not raw_path:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "Missing path query parameter."})
                    return
                removed = self._delete_upload(raw_path, self._uploads_root(owner_id))
                self._write_json(HTTPStatus.OK, removed)
                return

            if path.startswith("/jobs/"):
                job_id = path.split("/")[2]
                result = SERVICE.runtime_manager.delete_job(job_id, owner_id)
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
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise ValueError(f"JSON request body must not exceed {MAX_JSON_BODY_BYTES} bytes.")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _write_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        set_auth_token: str | None = None,
        clear_auth: bool = False,
        set_remember_token: str | None = None,
        clear_remember: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._send_session_cookie()
        # 显式设置/清除 cookie（必须在 send_response 之后、end_headers 之前）
        if set_auth_token:
            self._set_session_cookie(set_auth_token)
        if clear_auth:
            self._set_session_cookie("", clear=True)
        if set_remember_token:
            self._set_remember_cookie(set_remember_token)
        if clear_remember:
            self._set_remember_cookie("", clear=True)
        # 若已登录且无显式操作时，刷新 auth session cookie 以滚动过期时间；数据库未就绪时静默跳过。
        if not set_auth_token and not clear_auth:
            try:
                token = self._session_token()
                if token and auth_service.get_user_by_token(token):
                    self._set_session_cookie(token)
            except Exception:
                pass
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _write_text_attachment(self, status: HTTPStatus, text: str, *, filename: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self._send_session_cookie()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        encoded_name = quote(filename)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self._send_session_cookie()
        self.send_header("Location", location)
        self._send_security_headers()
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

    def _serve_project_file(self, raw_path: str, *, download: bool = False) -> None:
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
        self._send_file(resolved, allow_range=True, download=download)

    def _serve_project_file_for_owner(self, raw_path: str, owner_id: str, *, download: bool = False) -> None:
        if not self._public_mode:
            self._serve_project_file(raw_path, download=download)
            return
        try:
            resolved = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "File not found."})
            return
        allowed = False
        for job in SERVICE.runtime_manager._jobs.values():
            if job.record.owner_id != owner_id:
                continue
            for artifact in SERVICE.runtime_manager.list_job_artifacts(job.record.job_id, owner_id):
                try:
                    if resolved == Path(str(artifact.get("path") or "")).resolve(strict=True):
                        allowed = True
                        break
                except FileNotFoundError:
                    continue
            if allowed:
                break
        if not allowed:
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "Requested file is not an artifact owned by this session."})
            return
        self._send_file(resolved, allow_range=True, download=download)

    def _send_file(self, path: Path, *, allow_range: bool = False, download: bool = False) -> None:
        file_size = path.stat().st_size
        start = 0
        end = max(0, file_size - 1)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range", "") if allow_range else ""

        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or file_size <= 0:
                self._write_range_not_satisfiable(file_size)
                return
            start_text, end_text = match.groups()
            if not start_text:
                suffix_length = int(end_text or 0)
                if suffix_length <= 0:
                    self._write_range_not_satisfiable(file_size)
                    return
                start = max(0, file_size - suffix_length)
            else:
                start = int(start_text)
            if end_text and start_text:
                end = min(int(end_text), file_size - 1)
            if start >= file_size or start > end:
                self._write_range_not_satisfiable(file_size)
                return
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = end - start + 1 if file_size else 0
        content_type, _ = mimetypes.guess_type(str(path))
        if content_type and (
            content_type.startswith("text/")
            or content_type in {"application/json", "application/javascript"}
        ):
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(status)
        self._send_session_cookie()
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(content_length))
        self._send_security_headers()
        if allow_range:
            self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        if download:
            encoded_name = quote(path.name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
        self.end_headers()
        if content_length <= 0:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _write_range_not_satisfiable(self, file_size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self._send_session_cookie()
        self.send_header("Content-Range", f"bytes */{file_size}")
        self.send_header("Content-Length", "0")
        self.send_header("Accept-Ranges", "bytes")
        self._send_security_headers()
        self.end_headers()

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
    def _allocate_upload_path(filename: str, upload_dir: Path) -> Path:
        candidate = upload_dir / filename
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while True:
            deduped = upload_dir / f"{stem}_{index}{suffix}"
            if not deduped.exists():
                return deduped
            index += 1

    @staticmethod
    def _display_upload_path(path: Path, upload_dir: Path) -> str:
        relative = path.relative_to(upload_dir)
        return (Path("user_temp") / relative).as_posix()

    @classmethod
    def _serialize_upload_item(
        cls,
        path: Path,
        *,
        upload_dir: Path,
        analysis_index: dict[str, list[Path]] | None = None,
    ) -> dict[str, Any]:
        stat = path.stat()
        analysis_matches = match_analysis_files(path, analysis_index=analysis_index or {}) if is_video_file(path) else []
        latest_analysis = analysis_matches[0] if analysis_matches else None
        return {
            "name": path.name,
            "path": str(path.resolve()),
            "display_path": cls._display_upload_path(path, upload_dir),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "kind": "video" if is_video_file(path) else "file",
            "has_analysis": bool(analysis_matches),
            "analysis_count": len(analysis_matches),
            "analysis_path": str(latest_analysis.resolve()) if latest_analysis is not None else "",
            "analysis_display_path": cls._display_upload_path(latest_analysis, upload_dir) if latest_analysis is not None else "",
            "analysis_modified_at": (
                datetime.fromtimestamp(latest_analysis.stat().st_mtime, tz=timezone.utc).isoformat()
                if latest_analysis is not None
                else ""
            ),
        }

    @staticmethod
    def _filter_upload_items(items: list[dict[str, Any]], query: dict[str, list[str]]) -> list[dict[str, Any]]:
        """素材条件搜索：q 名称子串（忽略大小写）、has_analysis=1/0、sort、order。

        列表默认按修改时间倒序；sort 支持 modified_at/size_bytes/name。
        """
        keyword = (query.get("q", [""])[0] or "").strip().lower()
        if keyword:
            items = [item for item in items if keyword in str(item.get("name") or "").lower()]
        has_analysis = (query.get("has_analysis", [""])[0] or "").strip().lower()
        if has_analysis in {"1", "true", "yes"}:
            items = [item for item in items if item.get("has_analysis")]
        elif has_analysis in {"0", "false", "no"}:
            items = [item for item in items if not item.get("has_analysis")]
        sort_key = (query.get("sort", ["modified_at"])[0] or "modified_at").strip()
        key_funcs = {
            "name": lambda item: str(item.get("name") or "").lower(),
            "size_bytes": lambda item: int(item.get("size_bytes") or 0),
            "modified_at": lambda item: str(item.get("modified_at") or ""),
        }
        if sort_key in key_funcs:
            reverse = (query.get("order", ["desc"])[0] or "desc").strip().lower() != "asc"
            items = sorted(items, key=key_funcs[sort_key], reverse=reverse)
        return items

    @classmethod
    def _list_upload_items(cls, upload_dir: Path) -> list[dict[str, Any]]:
        analysis_index = build_analysis_index([upload_dir])
        items: list[dict[str, Any]] = []
        for path in sorted(upload_dir.rglob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
            if not is_video_file(path):
                continue
            items.append(cls._serialize_upload_item(path, upload_dir=upload_dir, analysis_index=analysis_index))
        return items

    @classmethod
    def _resolve_upload_path(cls, raw_path: str, upload_dir: Path) -> Path | None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (RUNTIME_ROOT / candidate).resolve(strict=False)
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(upload_dir.resolve())
        except Exception:
            resolved = None
        if resolved is None and raw_path.startswith("user_temp/"):
            # public mode 下 display_path 统一以 user_temp/ 前缀展示，但真实根是
            # public_uploads/<owner>/；回显删除/访问时按前缀剥离后落到 upload_dir。
            alt = (upload_dir / raw_path[len("user_temp/"):]).resolve(strict=False)
            try:
                alt.relative_to(upload_dir.resolve())
                resolved = alt
            except Exception:
                resolved = None
        return resolved

    def _handle_upload_request(self, upload_dir: Path, *, public: bool = False) -> list[dict[str, Any]]:
        if cgi is None:
            raise RuntimeError("Uploads require Python 3.12 or an updated multipart parser.")
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("Upload requests must use multipart/form-data.")
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if public and (content_length <= 0 or content_length > PUBLIC_UPLOAD_MAX_BYTES):
            raise ValueError(f"Each public upload must not exceed {PUBLIC_UPLOAD_MAX_BYTES // (1024 * 1024)} MB.")
        if public:
            used = sum(path.stat().st_size for path in upload_dir.rglob("*") if path.is_file())
            if used + content_length > PUBLIC_UPLOAD_SESSION_MAX_BYTES:
                raise ValueError("This session has reached its upload storage limit.")

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
            if public and Path(filename).suffix.lower() not in RuntimeManager.VIDEO_SUFFIXES:
                raise ValueError("Public uploads accept video files only.")

            target_name = self._sanitize_upload_name(filename)
            target_path = self._allocate_upload_path(target_name, upload_dir)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("wb") as handle:
                copied = 0
                while chunk := file_obj.read(1024 * 1024):
                    copied += len(chunk)
                    if public and copied > PUBLIC_UPLOAD_MAX_BYTES:
                        handle.close()
                        target_path.unlink(missing_ok=True)
                        raise ValueError(f"Each public upload must not exceed {PUBLIC_UPLOAD_MAX_BYTES // (1024 * 1024)} MB.")
                    handle.write(chunk)
            uploaded.append(self._serialize_upload_item(target_path, upload_dir=upload_dir))

        if not uploaded:
            raise ValueError("No valid files were uploaded.")

        return uploaded

    def _delete_upload(self, raw_path: str, upload_dir: Path) -> dict[str, Any]:
        resolved = self._resolve_upload_path(raw_path, upload_dir)
        if resolved is None:
            raise ValueError("Upload path is outside user_temp.")
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Upload not found: {raw_path}")
        deleted_analysis: list[dict[str, str]] = []
        if is_video_file(resolved):
            analysis_index = build_analysis_index([upload_dir])
            for analysis_path in match_analysis_files(resolved, analysis_index=analysis_index):
                if not analysis_path.exists() or not analysis_path.is_file():
                    continue
                try:
                    analysis_resolved = analysis_path.resolve(strict=False)
                    if analysis_resolved == resolved.resolve(strict=False):
                        continue
                    analysis_path.unlink()
                    deleted_analysis.append(
                        {
                            "path": str(analysis_resolved),
                            "display_path": self._display_upload_path(analysis_path, upload_dir),
                        }
                    )
                except FileNotFoundError:
                    continue
        resolved.unlink()
        return {
            "deleted": True,
            "path": str(resolved),
            "display_path": self._display_upload_path(resolved, upload_dir),
            "deleted_analysis_count": len(deleted_analysis),
            "deleted_analysis": deleted_analysis,
        }

    def _stream_events(self, job_id: str, after_sequence: int = 0, owner_id: str | None = None) -> None:
        self.send_response(HTTPStatus.OK)
        self._send_session_cookie()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_security_headers()
        self.end_headers()

        cursor = after_sequence
        try:
            while True:
                events = SERVICE.runtime_manager.wait_for_events(job_id, after_sequence=cursor, timeout=1.0, owner_id=owner_id)
                if events:
                    for event in events:
                        payload = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        cursor = max(cursor, int(event["sequence"]))
                else:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()

                job = SERVICE.runtime_manager.get_job(job_id, owner_id)
                if job is None:
                    break
                if job.record.status in {"completed", "failed", "cancelled", "interrupted"} and not events:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    break
        except (ConnectionError, BrokenPipeError):
            return

    @property
    def _public_mode(self) -> bool:
        return os.environ.get("CRAYOTTER_PUBLIC_MODE", "").strip().lower() in {"1", "true", "yes"}

    def _owner_id(self) -> str:
        if hasattr(self, "_session_owner"):
            return self._session_owner
        for item in self.headers.get("Cookie", "").split(";"):
            name, _, value = item.strip().partition("=")
            if name == "crayotter_session" and len(value) >= 32:
                self._session_owner, self._new_session = value, False
                return value
        self._session_owner, self._new_session = secrets.token_urlsafe(32), True
        return self._session_owner

    def _send_session_cookie(self) -> None:
        if getattr(self, "_new_session", False):
            secure = "; Secure" if os.environ.get("CRAYOTTER_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"} else ""
            self.send_header("Set-Cookie", f"crayotter_session={self._session_owner}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax{secure}")
            self._new_session = False

    def _uploads_root(self, owner_id: str) -> Path:
        if not self._public_mode:
            return UPLOADS_DIR
        root = PUBLIC_UPLOADS_DIR / owner_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _check_public_submission(self, owner_id: str) -> None:
        if self._public_mode:
            PUBLIC_TRIAL_GUARD.check_submission(owner_id)

    def _public_config(self) -> dict[str, Any]:
        config = SERVICE.config_store.load().model_dump()
        if not self._public_mode:
            return config
        operator_api_configured = False
        for profile in config.get("profiles", {}).values():
            for key in ("api_key", "video_api_key", "tts_api_key"):
                operator_api_configured = operator_api_configured or bool(profile.get(key))
                profile[key] = ""
        config["public_mode"] = True
        config["operator_api_configured"] = operator_api_configured
        return config

    def _public_runtime_overrides(self) -> dict[str, str]:
        """Read a bounded BYOK profile without persisting browser secrets."""
        headers = {
            "api_key": "X-Crayotter-Api-Key",
            "base_url": "X-Crayotter-Base-Url",
            "model_name": "X-Crayotter-Model-Name",
            "video_api_key": "X-Crayotter-Video-Api-Key",
            "video_base_url": "X-Crayotter-Video-Base-Url",
            "video_model_name": "X-Crayotter-Video-Model-Name",
            "tts_api_key": "X-Crayotter-Tts-Api-Key",
            "tts_base_url": "X-Crayotter-Tts-Base-Url",
            "tts_model_name": "X-Crayotter-Tts-Model-Name",
        }
        result: dict[str, str] = {}
        for field, header_name in headers.items():
            value = self.headers.get(header_name, "").strip()
            if value:
                if len(value) > 1024 or "\r" in value or "\n" in value:
                    raise ValueError(f"Invalid {header_name} header.")
                result[field] = value
        return result


    def _session_token(self) -> str | None:
        """从请求 Cookie 中读取 auth session token。"""
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return None
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(f"{SESSION_COOKIE_NAME}="):
                return part.split("=", 1)[1].strip()
        return None

    def _auth_user(self) -> dict[str, Any] | None:
        """返回当前登录用户，未登录返回 None。"""
        token = self._session_token()
        if not token:
            return None
        return auth_service.get_user_by_token(token)

    def _remember_cookie(self) -> str | None:
        """从请求 Cookie 中读取 remember token。"""
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return None
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(f"{REMEMBER_COOKIE_NAME}="):
                return part.split("=", 1)[1].strip()
        return None

    def _renew_from_remember(self) -> dict[str, Any] | None:
        """用 remember token 自动续期：轮换 remember 并创建新 session。"""
        renewed = auth_service.verify_and_rotate_remember_token(
            self._remember_cookie(),
            ip_address=self._client_ip(),
            user_agent=self._client_user_agent(),
        )
        if renewed is None:
            return None
        session_token, _ = auth_service.create_session(
            renewed["user"]["id"],
            ip_address=self._client_ip(),
            user_agent=self._client_user_agent(),
        )
        return {
            "user": renewed["user"],
            "session_token": session_token,
            "remember_token": renewed["new_token"],
        }

    def _set_remember_cookie(self, token: str, *, clear: bool = False) -> None:
        """设置或清除 remember cookie。"""
        if clear:
            self.send_header(
                "Set-Cookie",
                f"{REMEMBER_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
            )
            return
        secure = os.environ.get("CRAYOTTER_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
        flags = "HttpOnly; SameSite=Lax"
        if secure:
            flags += "; Secure"
        self.send_header(
            "Set-Cookie",
            f"{REMEMBER_COOKIE_NAME}={token}; Path=/; Max-Age={REMEMBER_COOKIE_MAX_AGE_SECONDS}; {flags}",
        )

    def _set_session_cookie(self, token: str, *, clear: bool = False) -> None:
        """设置或清除 auth session cookie。"""
        if clear:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
            )
            return
        secure = os.environ.get("CRAYOTTER_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
        flags = "HttpOnly; SameSite=Lax"
        if secure:
            flags += "; Secure"
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_COOKIE_MAX_AGE_SECONDS}; {flags}",
        )

    def _client_ip(self) -> str | None:
        """优先读取 X-Forwarded-For，否则取 remote address。"""
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else None

    def _client_user_agent(self) -> str | None:
        return self.headers.get("User-Agent") or None

    def _send_security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def build_http_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), BackendHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Crayotter backend service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    httpd = build_http_server(host=args.host, port=args.port)

    # 优雅停机：SIGTERM/SIGINT 触发 httpd.shutdown（systemd KillMode=control-group
    # 默认先发 SIGTERM），serve_forever 返回后把未完成任务落盘为 interrupted，
    # 避免被 TimeoutStopSec 升级为 SIGKILL 时 summary 来不及写。
    def _graceful_shutdown(signum, frame) -> None:
        SERVICE.runtime_manager.begin_shutdown()
        threading.Thread(target=httpd.shutdown, name="http-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    SERVICE.runtime_manager.start_janitor()

    print(f"Crayotter backend listening on http://{args.host}:{args.port}")
    print(f"Crayotter workbench available at http://{args.host}:{args.port}/ui/")
    try:
        httpd.serve_forever()
    finally:
        try:
            SERVICE.runtime_manager.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
