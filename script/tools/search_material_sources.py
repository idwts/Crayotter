from __future__ import annotations

from ._shared import *
from .material_sources import merge_candidates, normalize_candidate
from .browser_auth import BrowserAuthBroker, BrowserAuthRequest
from .source_adapters import (
    OptionalPlaywrightRenderer,
    SearchRequest,
    build_default_registry,
)


class _RuntimeAuthBroker:
    """Bridge the private browser broker to the adapter's in-memory protocol."""

    def __init__(self) -> None:
        self.broker = BrowserAuthBroker()
        self.events: list[dict[str, Any]] = []

    def storage_state(self, platform: str, profile: str = "") -> dict[str, Any] | None:
        browser = str(os.environ.get("CRAYOTTER_BROWSER_AUTH_BROWSER", "") or "").strip()
        selected_profile = str(
            profile or os.environ.get("CRAYOTTER_BROWSER_AUTH_PROFILE", "") or ""
        ).strip()
        result = self.broker.authorize(
            BrowserAuthRequest(
                browser=browser or "chrome",
                profile=selected_profile or None,
                platform=platform,
                workspace=WORKSPACE,
            )
        )
        self.events.append(result.as_event())
        if not result.authorized:
            return None
        state = self.broker.get_storage_state(result.session_handle)
        self.broker.release(result.session_handle)
        return state


_AUTH_BROKER = _RuntimeAuthBroker()
_SOURCE_REGISTRY: Any = None
_SOURCE_REGISTRY_KEY: tuple[str, str] | None = None


def cleanup_runtime_source_sessions() -> None:
    """Close in-memory browser state and remove private job authorization files."""
    _AUTH_BROKER.broker.cleanup_workspace(WORKSPACE)
    _AUTH_BROKER.broker.close()
    _AUTH_BROKER.events.clear()


def _source_registry():
    global _SOURCE_REGISTRY, _SOURCE_REGISTRY_KEY
    mode = str(os.environ.get("CRAYOTTER_YOUTUBE_MODE", "auto") or "auto").strip().lower()
    browser = str(os.environ.get("CRAYOTTER_BROWSER_AUTH_BROWSER", "") or "").strip().lower()
    key = (mode, browser)
    if _SOURCE_REGISTRY is None or _SOURCE_REGISTRY_KEY != key:
        _SOURCE_REGISTRY = build_default_registry(
            browser_renderer=OptionalPlaywrightRenderer(browser_channel=browser or None),
            auth_broker=_AUTH_BROKER,
        )
        _SOURCE_REGISTRY_KEY = key
    return _SOURCE_REGISTRY


@tool
def search_material_sources(
    query: str,
    platforms: list[str] | None = None,
    max_results: int = 5,
    pages: int = 2,
    expand_variants: int = 3,
    max_total_results: int | None = None,
    request_concurrency: int | None = None,
) -> str:
    """Search configured material platforms through policy-checked adapters."""
    configured = [
        item.strip().lower()
        for item in str(
            os.environ.get(
                "CRAYOTTER_ENABLED_MATERIAL_PLATFORMS",
                "bilibili,douyin,xiaohongshu,youtube",
            )
        ).split(",")
        if item.strip()
    ]
    requested = [str(item).strip().lower() for item in (platforms or configured) if str(item).strip()]
    if not requested:
        requested = configured or ["bilibili"]

    all_candidates: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    auth_event_offset = len(_AUTH_BROKER.events)
    auth_profile = str(os.environ.get("CRAYOTTER_BROWSER_AUTH_PROFILE", "") or "").strip()
    per_platform_limit = min(
        max(1, int(max_results or 5)),
        max(1, int(max_total_results or max_results or 5)),
    )
    for platform in requested:
        result = _source_registry().search(
            SearchRequest(
                query=query,
                platform=platform,
                limit=per_platform_limit,
                page_budget=max(1, int(pages or 1)),
                timeout_seconds=12.0,
                auth_profile=auth_profile,
            )
        )
        result_payload = result.to_dict()
        source_results.append(result_payload)
        all_candidates.extend(
            normalize_candidate(item, source=platform, query=query)
            for item in result.candidates
            if isinstance(item, dict)
        )

    merged = merge_candidates(all_candidates)[: max_total_results or len(all_candidates)]
    _append_candidates_to_pool(merged)
    return json.dumps(
        {
            "candidates": merged,
            "sources": source_results,
            "authorization": list(_AUTH_BROKER.events[auth_event_offset:]),
        },
        ensure_ascii=False,
    )
