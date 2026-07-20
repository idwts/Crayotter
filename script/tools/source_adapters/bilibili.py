from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from .models import AdapterError, AdapterErrorCode, AdapterStatus, SearchRequest, SourceSearchResult
from .normalization import merge_adapter_candidates, normalize_adapter_candidate


class BilibiliAdapter:
    name = "bilibili"
    platform = "bilibili"
    capabilities = frozenset({"metadata_probe"})

    def __init__(self, search_callable: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self._search_callable = search_callable

    def _invoke(self, arguments: dict[str, Any]) -> Any:
        if self._search_callable is not None:
            return self._search_callable(arguments)
        from ..search_bilibili_video import search_bilibili_video

        return search_bilibili_video.invoke(arguments)

    def search(self, request: SearchRequest) -> SourceSearchResult:
        started = time.monotonic()
        try:
            raw = self._invoke(
                {
                    "query": request.query,
                    "max_results": request.bounded_limit(),
                    "pages": max(1, int(request.page_budget or 1)),
                    "expand_variants": 1,
                    "max_total_results": request.bounded_limit(),
                }
            )
            parsed = json.loads(str(raw)) if not isinstance(raw, list) else raw
            if not isinstance(parsed, list):
                raise ValueError("Bilibili search returned a non-list response")
            candidates = merge_adapter_candidates(
                [normalize_adapter_candidate(item, platform=self.platform, query=request.query) for item in parsed if isinstance(item, dict)]
            )[: request.bounded_limit()]
            status = AdapterStatus.SUCCESS if candidates else AdapterStatus.ERROR
            errors = [] if candidates else [AdapterError(AdapterErrorCode.NOT_FOUND, "no Bilibili candidates found")]
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
            provenance={"method": "bilibili_api"},
        )
