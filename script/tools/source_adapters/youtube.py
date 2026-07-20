from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .models import AdapterError, AdapterErrorCode, AdapterStatus, SearchRequest, SourceSearchResult
from .normalization import merge_adapter_candidates, normalize_adapter_candidate


class YouTubeNetworkProbe:
    """Short, task-cacheable reachability probe for conditional YouTube use."""

    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self._opener = opener or urllib.request.urlopen
        self._cache: bool | None = None

    def available(self, timeout_seconds: float = 3.0) -> bool:
        if self._cache is not None:
            return self._cache
        request = urllib.request.Request(
            "https://www.youtube.com/results?search_query=crayotter_connectivity_check",
            headers={"User-Agent": "Mozilla/5.0 Crayotter/1.0"},
        )
        try:
            response = self._opener(request, timeout=max(0.5, min(timeout_seconds, 5.0)))
            status = int(getattr(response, "status", 200) or 200)
            self._cache = 200 <= status < 500
            close = getattr(response, "close", None)
            if callable(close):
                close()
        except (OSError, urllib.error.URLError, TimeoutError):
            self._cache = False
        return bool(self._cache)


class ConditionalYouTubeAdapter:
    name = "youtube"
    platform = "youtube"
    capabilities = frozenset({"metadata_probe"})

    def __init__(
        self,
        *,
        mode: str | None = None,
        probe: YouTubeNetworkProbe | None = None,
        search_callable: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        selected = str(mode or os.environ.get("CRAYOTTER_YOUTUBE_MODE", "auto")).strip().lower()
        self.mode = selected if selected in {"auto", "on", "off"} else "auto"
        self.probe = probe or YouTubeNetworkProbe()
        self._search_callable = search_callable

    def _invoke(self, arguments: dict[str, Any]) -> Any:
        if self._search_callable is not None:
            return self._search_callable(arguments)
        from ..search_youtobe_video import search_youtobe_video

        return search_youtobe_video.invoke(arguments)

    def search(self, request: SearchRequest) -> SourceSearchResult:
        started = time.monotonic()
        if self.mode == "off":
            return self._skipped(started, "YouTube is disabled by configuration")
        if self.mode == "auto" and not self.probe.available(min(request.timeout_seconds, 3.0)):
            return self._skipped(started, "YouTube is not reachable in the current network environment")
        try:
            raw = self._invoke(
                {
                    "query": request.query,
                    "max_results": request.bounded_limit(),
                    "expand_variants": 1,
                    "max_total_results": request.bounded_limit(),
                }
            )
            parsed = json.loads(str(raw)) if not isinstance(raw, list) else raw
            if not isinstance(parsed, list):
                raise ValueError("YouTube search returned a non-list response")
            candidates = merge_adapter_candidates(
                [normalize_adapter_candidate(item, platform=self.platform, query=request.query) for item in parsed if isinstance(item, dict)]
            )[: request.bounded_limit()]
            errors = [] if candidates else [AdapterError(AdapterErrorCode.NOT_FOUND, "no YouTube candidates found")]
            status = AdapterStatus.SUCCESS if candidates else AdapterStatus.ERROR
        except Exception as exc:
            candidates = []
            status = AdapterStatus.ERROR
            errors = [AdapterError(AdapterErrorCode.INTERNAL_ERROR, str(exc), retryable=True)]
        return SourceSearchResult(
            platform=self.platform,
            status=status,
            candidates=candidates,
            errors=errors,
            latency_seconds=time.monotonic() - started,
            provenance={"method": "yt_dlp_search", "mode": self.mode},
        )

    def _skipped(self, started: float, message: str) -> SourceSearchResult:
        return SourceSearchResult(
            platform=self.platform,
            status=AdapterStatus.SKIPPED,
            errors=[AdapterError(AdapterErrorCode.NETWORK_UNAVAILABLE, message)],
            latency_seconds=time.monotonic() - started,
            provenance={"method": "network_probe", "mode": self.mode},
        )
