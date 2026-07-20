from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AdapterStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    AUTHORIZATION_REQUIRED = "authorization_required"
    ERROR = "error"


class AdapterErrorCode(str, Enum):
    AUTH_REQUIRED = "auth_required"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    RATE_LIMITED = "rate_limited"
    CAPTCHA_REQUIRED = "captcha_required"
    CHALLENGE_DETECTED = "challenge_detected"
    MEDIA_URL_FORBIDDEN = "media_url_forbidden"
    GEO_BLOCKED = "geo_blocked"
    EXTRACTOR_BROKEN = "extractor_broken"
    TIMEOUT = "timeout"
    NETWORK_UNAVAILABLE = "network_unavailable"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    NOT_FOUND = "not_found"
    POLICY_BLOCKED = "policy_blocked"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class AdapterError:
    code: AdapterErrorCode
    message: str
    retryable: bool = False
    authorization_supported: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["code"] = self.code.value
        return value


@dataclass(frozen=True)
class SearchRequest:
    query: str
    platform: str = ""
    limit: int = 5
    page_budget: int = 1
    timeout_seconds: float = 12.0
    orientation: str = ""
    auth_profile: str = ""

    def bounded_limit(self) -> int:
        return max(1, min(int(self.limit or 1), 80))


@dataclass
class SourceSearchResult:
    platform: str
    status: AdapterStatus
    candidates: list[dict[str, Any]] = field(default_factory=list)
    errors: list[AdapterError] = field(default_factory=list)
    latency_seconds: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": self.status.value,
            "candidates": self.candidates,
            "errors": [error.to_dict() for error in self.errors],
            "latency_seconds": round(max(0.0, self.latency_seconds), 3),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    platform: str
    destination: str | Path
    timeout_seconds: float = 30.0
    auth_profile: str = ""
    user_agent: str = ""


@dataclass
class SourceDownloadResult:
    platform: str
    status: AdapterStatus
    path: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    errors: list[AdapterError] = field(default_factory=list)
    latency_seconds: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": self.status.value,
            "path": self.path,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "errors": [error.to_dict() for error in self.errors],
            "latency_seconds": round(max(0.0, self.latency_seconds), 3),
            "provenance": self.provenance,
        }


@dataclass
class BrowserPage:
    url: str
    html: str
    media_urls: list[str] = field(default_factory=list)
    status_code: int | None = None
    authorization_used: bool = False
    request_headers: dict[str, str] = field(default_factory=dict)
    # Ephemeral cookies are intentionally omitted from every result serializer.
    cookies: list[dict[str, Any]] = field(default_factory=list, repr=False)


AUTHORIZATION_ERROR_CODES = frozenset(
    {
        AdapterErrorCode.AUTH_REQUIRED,
        AdapterErrorCode.CAPTCHA_REQUIRED,
        AdapterErrorCode.CHALLENGE_DETECTED,
        AdapterErrorCode.MEDIA_URL_FORBIDDEN,
        AdapterErrorCode.RATE_LIMITED,
    }
)
