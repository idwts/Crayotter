"""Policy-checked material source adapters.

The package deliberately has no hard dependency on Playwright.  Callers can use
``build_default_registry`` in headless environments and receive structured
adapter errors when browser rendering is unavailable.
"""

from .base import AuthBroker, BrowserRenderer, MaterialSourceAdapter, MediaDownloader
from .bilibili import BilibiliAdapter
from .browser import OptionalPlaywrightRenderer
from .crawler import DouyinCrawlerAdapter, XiaohongshuCrawlerAdapter
from .models import (
    AdapterError,
    AdapterErrorCode,
    AdapterStatus,
    BrowserPage,
    DownloadRequest,
    SearchRequest,
    SourceDownloadResult,
    SourceSearchResult,
)
from .registry import AdapterPolicyError, SourceAdapterRegistry, build_default_registry
from .youtube import ConditionalYouTubeAdapter, YouTubeNetworkProbe

__all__ = [
    "AdapterError",
    "AdapterErrorCode",
    "AdapterPolicyError",
    "AdapterStatus",
    "AuthBroker",
    "BilibiliAdapter",
    "BrowserPage",
    "BrowserRenderer",
    "ConditionalYouTubeAdapter",
    "DouyinCrawlerAdapter",
    "DownloadRequest",
    "MaterialSourceAdapter",
    "MediaDownloader",
    "OptionalPlaywrightRenderer",
    "SearchRequest",
    "SourceAdapterRegistry",
    "SourceDownloadResult",
    "SourceSearchResult",
    "XiaohongshuCrawlerAdapter",
    "YouTubeNetworkProbe",
    "build_default_registry",
]
