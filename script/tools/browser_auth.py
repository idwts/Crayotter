from __future__ import annotations

import copy
import secrets
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple


AUTH_SESSION_DIR = ".crayotter/browser_auth"

PLATFORM_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "rednote.com"),
    "rednote": ("xiaohongshu.com", "xhslink.com", "rednote.com"),
}

PLATFORM_LOGIN_URLS = {
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "rednote": "https://www.xiaohongshu.com/",
}

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "cookie_header",
        "password",
        "secret",
        "storage_state",
        "token",
        "value",
    }
)


@dataclass(frozen=True)
class BrowserAuthRequest:
    """An authorization request without any credential material."""

    browser: str
    profile: Optional[str]
    platform: str
    workspace: Path


@dataclass(frozen=True)
class BrowserAuthResult:
    """Authorization outcome; use ``as_event`` for safe serialization."""

    status: str
    platform: str
    reason: str = ""
    session_handle: str = field(default="", repr=False)
    cookie_count: int = 0
    allowed_domains: Tuple[str, ...] = field(default_factory=tuple)
    manual_session_id: str = ""

    @property
    def authorized(self) -> bool:
        return self.status == "authorized" and bool(self.session_handle)

    def as_event(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "platform": self.platform,
            "reason": self.reason,
            "authorized": self.authorized,
            "cookie_count": self.cookie_count,
            "allowed_domains": list(self.allowed_domains),
            "manual_session_id": self.manual_session_id,
        }


@dataclass
class ManualAuthorizationStart:
    result: BrowserAuthResult
    session: Optional["ManualAuthorizationSession"] = field(default=None, repr=False)


CookieLoader = Callable[[BrowserAuthRequest], Iterable[Mapping[str, Any]]]


def redact_sensitive(value: Any) -> Any:
    """Recursively redact credential-shaped values before diagnostics are emitted."""

    if isinstance(value, Mapping):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_sensitive(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value


def filter_platform_cookies(
    platform: str,
    cookies: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return Playwright-compatible cookies limited to the requested platform."""

    allowed = PLATFORM_DOMAINS.get(_normalize_platform(platform), ())
    filtered: List[Dict[str, Any]] = []
    for raw_cookie in cookies:
        if not isinstance(raw_cookie, Mapping):
            continue
        domain = str(raw_cookie.get("domain") or "").strip().lower().lstrip(".")
        if not domain or not any(_domain_matches(domain, suffix) for suffix in allowed):
            continue
        name = str(raw_cookie.get("name") or "").strip()
        value = raw_cookie.get("value")
        if not name or value is None:
            continue
        cookie: Dict[str, Any] = {
            "name": name,
            "value": str(value),
            "domain": str(raw_cookie.get("domain") or domain),
            "path": str(raw_cookie.get("path") or "/"),
        }
        for key in ("expires", "httpOnly", "secure", "sameSite"):
            if key in raw_cookie and raw_cookie[key] is not None:
                cookie[key] = raw_cookie[key]
        filtered.append(cookie)
    return filtered


class BrowserAuthBroker:
    """Owns short-lived browser authorization without exposing cookie material.

    Imported browser-profile state is kept in memory. Isolated manual-login state
    may exist under the supplied job workspace while a live session is open, and
    is removed on completion, cancellation, or explicit workspace cleanup.
    """

    def __init__(self, cookie_loader: Optional[CookieLoader] = None) -> None:
        self._cookie_loader = cookie_loader
        self._storage_states: Dict[str, Dict[str, Any]] = {}
        self._manual_directories: Dict[str, Path] = {}
        self._manual_sessions: Dict[str, ManualAuthorizationSession] = {}

    def authorize(self, request: BrowserAuthRequest) -> BrowserAuthResult:
        platform = _normalize_platform(request.platform)
        domains = PLATFORM_DOMAINS.get(platform, ())
        if not domains:
            return BrowserAuthResult(
                status="unsupported_platform",
                platform=platform,
                reason="platform_not_supported",
            )
        if not request.profile:
            return self.authorization_required(platform, "profile_required")

        try:
            if self._cookie_loader is not None:
                cookies = list(self._cookie_loader(request))
            else:
                cookies = self._load_profile_cookies_with_playwright(request)
        except ModuleNotFoundError:
            reason = "playwright_unavailable" if self._cookie_loader is None else "profile_read_failed"
            return self.authorization_required(platform, reason)
        except (FileNotFoundError, NotADirectoryError):
            return self.authorization_required(platform, "profile_unavailable")
        except Exception:
            # Never include browser errors: Chromium commonly embeds command-line
            # arguments, profile paths, or fragments of page state in them.
            return self.authorization_required(platform, "profile_read_failed")

        return self._register_cookies(platform, cookies, empty_reason="no_platform_cookies")

    def authorization_required(self, platform: str, reason: str) -> BrowserAuthResult:
        normalized = _normalize_platform(platform)
        return BrowserAuthResult(
            status="auth_required",
            platform=normalized,
            reason=reason,
            allowed_domains=PLATFORM_DOMAINS.get(normalized, ()),
        )

    def get_storage_state(self, session_handle: str) -> Optional[Dict[str, Any]]:
        """Resolve a private handle for immediate adapter use.

        Callers must not serialize or log the returned mapping.
        """

        state = self._storage_states.get(str(session_handle or ""))
        return copy.deepcopy(state) if state is not None else None

    def release(self, session_handle: str) -> bool:
        state = self._storage_states.pop(str(session_handle or ""), None)
        if state is not None:
            _scrub_mapping(state)
            return True
        return False

    def begin_manual_authorization(
        self,
        request: BrowserAuthRequest,
        *,
        login_url: Optional[str] = None,
    ) -> ManualAuthorizationStart:
        platform = _normalize_platform(request.platform)
        domains = PLATFORM_DOMAINS.get(platform, ())
        if not domains:
            result = BrowserAuthResult(
                status="unsupported_platform",
                platform=platform,
                reason="platform_not_supported",
            )
            return ManualAuthorizationStart(result=result)

        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            result = self.authorization_required(platform, "playwright_unavailable")
            return ManualAuthorizationStart(result=result)

        manual_id = secrets.token_urlsafe(18)
        session_dir = self._create_manual_directory(request.workspace, platform, manual_id)
        playwright_manager = None
        context = None
        try:
            playwright_manager = sync_playwright()
            playwright = playwright_manager.start()
            browser_type, channel = _resolve_browser(playwright, request.browser)
            kwargs: Dict[str, Any] = {
                "user_data_dir": str(session_dir),
                "headless": False,
            }
            if channel:
                kwargs["channel"] = channel
            context = browser_type.launch_persistent_context(**kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                login_url or PLATFORM_LOGIN_URLS[platform],
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception:
            if context is not None:
                _safe_close(context)
            if playwright_manager is not None:
                _safe_close(playwright_manager)
            self._secure_remove_directory(session_dir, request.workspace)
            result = self.authorization_required(platform, "manual_browser_launch_failed")
            return ManualAuthorizationStart(result=result)

        self._manual_directories[manual_id] = session_dir
        session = ManualAuthorizationSession(
            broker=self,
            request=request,
            manual_session_id=manual_id,
            context=context,
            playwright_manager=playwright_manager,
            session_dir=session_dir,
        )
        self._manual_sessions[manual_id] = session
        result = BrowserAuthResult(
            status="authorization_pending",
            platform=platform,
            reason="manual_login_required",
            allowed_domains=domains,
            manual_session_id=manual_id,
        )
        return ManualAuthorizationStart(result=result, session=session)

    def cleanup_workspace(self, workspace: Path) -> None:
        root = _manual_root(workspace)
        for session in list(self._manual_sessions.values()):
            try:
                session._session_dir.resolve().relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            session.close()
        for manual_id, directory in list(self._manual_directories.items()):
            try:
                directory.resolve().relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            self._manual_directories.pop(manual_id, None)
        self._secure_remove_directory(root, workspace, root_is_allowed=True)

    def close(self) -> None:
        for session in list(self._manual_sessions.values()):
            session.close()
        for handle in list(self._storage_states):
            self.release(handle)

    def _register_cookies(
        self,
        platform: str,
        cookies: Iterable[Mapping[str, Any]],
        *,
        empty_reason: str,
    ) -> BrowserAuthResult:
        filtered = filter_platform_cookies(platform, cookies)
        if not filtered:
            return self.authorization_required(platform, empty_reason)
        handle = secrets.token_urlsafe(24)
        self._storage_states[handle] = {"cookies": filtered, "origins": []}
        return BrowserAuthResult(
            status="authorized",
            platform=platform,
            session_handle=handle,
            cookie_count=len(filtered),
            allowed_domains=PLATFORM_DOMAINS[platform],
        )

    def _complete_manual_session(
        self,
        session: "ManualAuthorizationSession",
    ) -> BrowserAuthResult:
        platform = _normalize_platform(session.request.platform)
        try:
            cookies = session.context.cookies()
        except Exception:
            return self.authorization_required(platform, "manual_cookie_read_failed")
        return self._register_cookies(platform, cookies, empty_reason="manual_login_incomplete")

    def _load_profile_cookies_with_playwright(
        self,
        request: BrowserAuthRequest,
    ) -> List[Dict[str, Any]]:
        from playwright.sync_api import sync_playwright

        profile_path = Path(str(request.profile)).expanduser().resolve()
        if not profile_path.is_dir():
            raise FileNotFoundError(str(profile_path))

        profile_name = ""
        user_data_dir = profile_path
        if profile_path.name == "Default" or profile_path.name.startswith("Profile "):
            user_data_dir = profile_path.parent
            profile_name = profile_path.name

        with sync_playwright() as playwright:
            browser_type, channel = _resolve_browser(playwright, request.browser)
            kwargs: Dict[str, Any] = {
                "user_data_dir": str(user_data_dir),
                "headless": True,
            }
            if channel:
                kwargs["channel"] = channel
            if profile_name:
                kwargs["args"] = ["--profile-directory=" + profile_name]
            context = browser_type.launch_persistent_context(**kwargs)
            try:
                return list(context.cookies())
            finally:
                context.close()

    def _create_manual_directory(self, workspace: Path, platform: str, manual_id: str) -> Path:
        root = _manual_root(workspace)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _restrict_permissions(root)
        directory = root / (platform + "-" + manual_id)
        directory.mkdir(mode=0o700)
        _restrict_permissions(directory)
        return directory

    def _secure_remove_directory(
        self,
        directory: Path,
        workspace: Path,
        *,
        root_is_allowed: bool = False,
    ) -> None:
        workspace_root = Path(workspace).expanduser().resolve()
        allowed_root = _manual_root(workspace_root).resolve()
        target = Path(directory).expanduser().resolve()
        if target == allowed_root:
            if not root_is_allowed:
                return
        else:
            try:
                target.relative_to(allowed_root)
            except ValueError:
                return
        if workspace_root == target or workspace_root not in target.parents:
            return
        if target.exists():
            shutil.rmtree(str(target), ignore_errors=True)


class ManualAuthorizationSession:
    def __init__(
        self,
        *,
        broker: BrowserAuthBroker,
        request: BrowserAuthRequest,
        manual_session_id: str,
        context: Any,
        playwright_manager: Any,
        session_dir: Path,
    ) -> None:
        self._broker = broker
        self.request = request
        self.manual_session_id = manual_session_id
        self.context = context
        self._playwright_manager = playwright_manager
        self._session_dir = session_dir
        self._closed = False

    def __repr__(self) -> str:
        return "ManualAuthorizationSession(platform={!r}, closed={!r})".format(
            _normalize_platform(self.request.platform), self._closed
        )

    def complete(self) -> BrowserAuthResult:
        if self._closed:
            return self._broker.authorization_required(
                self.request.platform, "manual_session_closed"
            )
        try:
            return self._broker._complete_manual_session(self)
        finally:
            self.close()

    def cancel(self) -> BrowserAuthResult:
        self.close()
        return self._broker.authorization_required(self.request.platform, "authorization_cancelled")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _safe_close(self.context)
        _safe_close(self._playwright_manager)
        self._broker._manual_sessions.pop(self.manual_session_id, None)
        self._broker._manual_directories.pop(self.manual_session_id, None)
        self._broker._secure_remove_directory(self._session_dir, self.request.workspace)

    def __enter__(self) -> "ManualAuthorizationSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _normalize_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    return "xiaohongshu" if normalized == "rednote" else normalized


def _domain_matches(domain: str, allowed_suffix: str) -> bool:
    return domain == allowed_suffix or domain.endswith("." + allowed_suffix)


def _manual_root(workspace: Path) -> Path:
    return Path(workspace).expanduser().resolve().joinpath(*AUTH_SESSION_DIR.split("/"))


def _restrict_permissions(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def _safe_close(resource: Any) -> None:
    try:
        resource.close() if hasattr(resource, "close") else resource.stop()
    except Exception:
        pass


def _resolve_browser(playwright: Any, browser: str) -> Tuple[Any, Optional[str]]:
    normalized = str(browser or "").strip().lower()
    if normalized in {"chrome", "google-chrome"}:
        return playwright.chromium, "chrome"
    if normalized in {"edge", "msedge", "microsoft-edge"}:
        return playwright.chromium, "msedge"
    if normalized in {"chromium", ""}:
        return playwright.chromium, None
    raise ValueError("unsupported browser")


def _scrub_mapping(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, (dict, list)):
                _scrub_mapping(item)
            value[key] = None
        value.clear()
    elif isinstance(value, list):
        for item in value:
            _scrub_mapping(item)
        value.clear()
