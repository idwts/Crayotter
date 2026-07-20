from __future__ import annotations

from collections.abc import Iterable

from ..material_source_policy import validate_material_source_capabilities
from .base import AuthBroker, BrowserRenderer, MaterialSourceAdapter
from .bilibili import BilibiliAdapter
from .crawler import DouyinCrawlerAdapter, XiaohongshuCrawlerAdapter
from .models import (
    AdapterError,
    AdapterErrorCode,
    AdapterStatus,
    DownloadRequest,
    SearchRequest,
    SourceDownloadResult,
    SourceSearchResult,
)
from .youtube import ConditionalYouTubeAdapter


class AdapterPolicyError(ValueError):
    pass


class SourceAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, MaterialSourceAdapter] = {}

    def register(self, adapter: MaterialSourceAdapter) -> None:
        platform = str(adapter.platform or "").strip().lower()
        if not platform:
            raise ValueError("adapter platform must not be empty")
        decision = validate_material_source_capabilities(adapter.name, adapter.capabilities)
        if decision["status"] != "allowed":
            blocked = ", ".join(decision["blocked_capabilities"])
            raise AdapterPolicyError(f"adapter {adapter.name!r} requests blocked capabilities: {blocked}")
        self._adapters[platform] = adapter

    def get(self, platform: str) -> MaterialSourceAdapter | None:
        return self._adapters.get(str(platform or "").strip().lower())

    def platforms(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def search(self, request: SearchRequest) -> SourceSearchResult:
        platform = str(request.platform or "").strip().lower()
        adapter = self.get(platform)
        if adapter is None:
            return SourceSearchResult(
                platform=platform or "unknown",
                status=AdapterStatus.SKIPPED,
                errors=[AdapterError(AdapterErrorCode.NOT_FOUND, f"no adapter registered for {platform or 'unknown'}")],
            )
        return adapter.search(request)

    def search_many(self, requests: Iterable[SearchRequest]) -> list[SourceSearchResult]:
        # Scheduling/concurrency intentionally belongs to ResourceScheduler.
        return [self.search(request) for request in requests]

    def download(self, request: DownloadRequest) -> SourceDownloadResult:
        platform = str(request.platform or "").strip().lower()
        adapter = self.get(platform)
        download = getattr(adapter, "download", None) if adapter is not None else None
        if not callable(download):
            return SourceDownloadResult(
                platform=platform or "unknown",
                status=AdapterStatus.SKIPPED,
                errors=[
                    AdapterError(
                        AdapterErrorCode.NOT_FOUND,
                        f"no download adapter registered for {platform or 'unknown'}",
                    )
                ],
            )
        return download(request)


def build_default_registry(
    *,
    browser_renderer: BrowserRenderer | None = None,
    auth_broker: AuthBroker | None = None,
) -> SourceAdapterRegistry:
    registry = SourceAdapterRegistry()
    registry.register(BilibiliAdapter())
    registry.register(ConditionalYouTubeAdapter())
    registry.register(DouyinCrawlerAdapter(browser_renderer=browser_renderer, auth_broker=auth_broker))
    registry.register(XiaohongshuCrawlerAdapter(browser_renderer=browser_renderer, auth_broker=auth_broker))
    return registry
