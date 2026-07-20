from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pathlib import Path

from .models import BrowserPage, DownloadRequest, SearchRequest, SourceDownloadResult, SourceSearchResult


@runtime_checkable
class AuthBroker(Protocol):
    """Supplies ephemeral Playwright storage state; implementations own cleanup."""

    def storage_state(self, platform: str, profile: str = "") -> dict[str, Any] | None:
        ...


@runtime_checkable
class BrowserRenderer(Protocol):
    def render(
        self,
        url: str,
        *,
        platform: str,
        timeout_seconds: float,
        auth_broker: AuthBroker | None = None,
        auth_profile: str = "",
    ) -> BrowserPage:
        ...


@runtime_checkable
class MediaDownloader(Protocol):
    """Downloads using the ephemeral headers/cookies captured from a detail page."""

    def download(
        self,
        media_url: str,
        destination: Path,
        *,
        page: BrowserPage,
        platform: str,
        timeout_seconds: float,
    ) -> tuple[str, int]:
        """Return ``(mime_type, size_bytes)`` after writing destination."""
        ...


@runtime_checkable
class MaterialSourceAdapter(Protocol):
    name: str
    platform: str
    capabilities: frozenset[str]

    def search(self, request: SearchRequest) -> SourceSearchResult:
        ...

    def download(self, request: DownloadRequest) -> SourceDownloadResult:
        ...
