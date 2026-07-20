from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# The adapter package is intentionally SDK-independent. Bypass the eager
# script.tools package initializer so these focused tests run without model SDKs.
if "script" not in sys.modules:
    script_package = types.ModuleType("script")
    script_package.__path__ = [str(SCRIPT_DIR)]
    sys.modules["script"] = script_package
if "script.tools" not in sys.modules:
    tools_package = types.ModuleType("script.tools")
    tools_package.__path__ = [str(SCRIPT_DIR / "tools")]
    sys.modules["script.tools"] = tools_package

from script.tools.source_adapters import (
    AdapterPolicyError,
    AdapterStatus,
    BilibiliAdapter,
    BrowserPage,
    ConditionalYouTubeAdapter,
    DouyinCrawlerAdapter,
    DownloadRequest,
    SearchRequest,
    SourceAdapterRegistry,
    XiaohongshuCrawlerAdapter,
    YouTubeNetworkProbe,
    build_default_registry,
)


class _Fetcher:
    def __init__(self, page: BrowserPage | None = None, error: Exception | None = None) -> None:
        self.page = page
        self.error = error
        self.calls: list[str] = []

    def fetch(self, url: str, *, timeout_seconds: float) -> BrowserPage:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        assert self.page is not None
        return self.page


class _Renderer:
    def __init__(self, anonymous: BrowserPage, authorized: BrowserPage | None = None) -> None:
        self.anonymous = anonymous
        self.authorized = authorized
        self.calls: list[dict[str, Any]] = []

    def render(self, url: str, **kwargs: Any) -> BrowserPage:
        self.calls.append(dict(kwargs))
        if kwargs.get("auth_profile"):
            assert self.authorized is not None
            return self.authorized
        return self.anonymous


class _AuthBroker:
    def storage_state(self, platform: str, profile: str = "") -> dict[str, Any] | None:
        return {"cookies": [{"name": "sensitive", "value": "must-not-leak"}]}


class _SequenceRenderer:
    def __init__(self, pages: list[BrowserPage]) -> None:
        self.pages = list(pages)
        self.calls: list[dict[str, Any]] = []

    def render(self, url: str, **kwargs: Any) -> BrowserPage:
        self.calls.append(dict(kwargs))
        if not self.pages:
            raise AssertionError("unexpected render")
        return self.pages.pop(0)


class _MediaDownloader:
    def __init__(self, failures: int = 0, *, mime_type: str = "video/mp4", valid_header: bool = True) -> None:
        self.failures = failures
        self.mime_type = mime_type
        self.valid_header = valid_header
        self.calls: list[dict[str, Any]] = []

    def download(self, media_url: str, destination: Path, **kwargs: Any) -> tuple[str, int]:
        self.calls.append({"media_url": media_url, **kwargs})
        if self.failures > 0:
            self.failures -= 1
            from script.tools.source_adapters.crawler import MediaDownloadFailure
            from script.tools.source_adapters.models import AdapterErrorCode

            raise MediaDownloadFailure(AdapterErrorCode.MEDIA_URL_FORBIDDEN, "expired", retryable=True)
        payload = b"\x00\x00\x00\x18ftypisom" + b"video-payload" if self.valid_header else b"not-a-video"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return self.mime_type, len(payload)


class SourceAdapterTests(unittest.TestCase):
    def test_douyin_rejects_untrusted_detail_and_media_urls(self) -> None:
        downloader = _MediaDownloader()
        adapter = DouyinCrawlerAdapter(
            fetcher=_Fetcher(
                BrowserPage(
                    "https://www.douyin.com/video/1",
                    "",
                    media_urls=["https://attacker.example/collect.mp4"],
                )
            ),
            media_downloader=downloader,
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            blocked_detail = adapter.download(
                DownloadRequest(
                    url="http://127.0.0.1/internal",
                    platform="douyin",
                    destination=Path(tmp) / "detail.mp4",
                )
            )
            blocked_media = adapter.download(
                DownloadRequest(
                    url="https://www.douyin.com/video/1",
                    platform="douyin",
                    destination=Path(tmp) / "media.mp4",
                )
            )

        self.assertEqual(blocked_detail.status, AdapterStatus.ERROR)
        self.assertEqual(blocked_media.status, AdapterStatus.ERROR)
        self.assertIn("policy_blocked", {error.code.value for error in blocked_media.errors})
        self.assertEqual(downloader.calls, [])

    def test_cookie_header_is_scoped_to_media_domain(self) -> None:
        from script.tools.source_adapters.crawler import _cookie_header_for_url

        cookies = [
            {"name": "platform", "value": "secret", "domain": ".douyin.com", "path": "/"},
            {"name": "cdn", "value": "allowed", "domain": ".zjcdn.com", "path": "/video"},
        ]
        header = _cookie_header_for_url(cookies, "https://v3-dy-o.zjcdn.com/video/tos/a.mp4")
        self.assertNotIn("platform=secret", header)
        self.assertIn("cdn=allowed", header)

    def test_douyin_extracts_public_initial_state_without_browser(self) -> None:
        state = {
            "items": [
                {
                    "aweme_id": "7336481666707229992",
                    "desc": "校园风景",
                    "author": {"nickname": "作者"},
                    "video": {"duration": 23000, "width": 1080, "height": 1920},
                }
            ]
        }
        html = f'<script id="RENDER_DATA" type="application/json">{json.dumps(state)}</script>'
        adapter = DouyinCrawlerAdapter(
            fetcher=_Fetcher(BrowserPage("https://www.douyin.com/search/test", html)),
            minimum_interval_seconds=0,
        )

        result = adapter.search(SearchRequest(query="校园", platform="douyin", limit=3))

        self.assertEqual(result.status, AdapterStatus.SUCCESS)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0]["id"], "7336481666707229992")
        self.assertEqual(result.candidates[0]["orientation_hint"], "portrait")
        self.assertEqual(result.provenance["methods_attempted"], ["public_page"])

    def test_xiaohongshu_falls_back_to_anonymous_browser_render(self) -> None:
        note_id = "6411cf99000000001300b6d9"
        renderer = _Renderer(
            BrowserPage(
                "https://www.xiaohongshu.com/search_result",
                f'<a href="/explore/{note_id}">校园生活</a>',
            )
        )
        adapter = XiaohongshuCrawlerAdapter(
            fetcher=_Fetcher(BrowserPage("https://www.xiaohongshu.com/search_result", "<html></html>")),
            browser_renderer=renderer,
            minimum_interval_seconds=0,
        )

        result = adapter.search(SearchRequest(query="校园", platform="xiaohongshu"))

        self.assertEqual(result.status, AdapterStatus.SUCCESS)
        self.assertEqual(result.candidates[0]["id"], note_id)
        self.assertEqual(result.provenance["methods_attempted"], ["public_page", "anonymous_browser"])
        self.assertEqual(renderer.calls[0]["auth_profile"], "")

    def test_challenge_requests_authorization_then_uses_broker_abstraction(self) -> None:
        authorized_html = (
            '<script type="application/json">'
            '{"aweme_id":"7336481666707229992","desc":"authorized result"}'
            "</script>"
        )
        renderer = _Renderer(
            BrowserPage("https://www.douyin.com/search/test", "请完成安全验证"),
            BrowserPage("https://www.douyin.com/search/test", authorized_html, authorization_used=True),
        )
        adapter = DouyinCrawlerAdapter(
            fetcher=_Fetcher(BrowserPage("https://www.douyin.com/search/test", "验证码")),
            browser_renderer=renderer,
            auth_broker=_AuthBroker(),
            minimum_interval_seconds=0,
        )

        result = adapter.search(
            SearchRequest(query="校园", platform="douyin", auth_profile="Default")
        )

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertEqual(result.candidates[0]["id"], "7336481666707229992")
        self.assertEqual(len(renderer.calls), 2)
        self.assertEqual(renderer.calls[1]["auth_profile"], "Default")
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("cookies", serialized)

    def test_challenge_without_profile_returns_structured_authorization_status(self) -> None:
        adapter = DouyinCrawlerAdapter(
            fetcher=_Fetcher(BrowserPage("https://www.douyin.com/search/test", "captcha")),
            browser_renderer=_Renderer(BrowserPage("https://www.douyin.com/search/test", "captcha")),
            minimum_interval_seconds=0,
        )

        result = adapter.search(SearchRequest(query="校园", platform="douyin"))

        self.assertEqual(result.status, AdapterStatus.AUTHORIZATION_REQUIRED)
        self.assertIn("captcha_required", {error.code.value for error in result.errors})

    def test_optional_browser_dependency_degrades_to_structured_error(self) -> None:
        adapter = XiaohongshuCrawlerAdapter(
            fetcher=_Fetcher(
                error=urllib.error.URLError("offline")
            ),
            minimum_interval_seconds=0,
        )

        result = adapter.search(SearchRequest(query="校园", platform="xiaohongshu"))

        self.assertEqual(result.status, AdapterStatus.ERROR)
        codes = {error.code.value for error in result.errors}
        self.assertTrue({"timeout", "dependency_unavailable"}.issubset(codes))

    def test_douyin_downloads_observed_media_without_ytdlp(self) -> None:
        media_url = "https://v3-dy-o.zjcdn.com/video/tos/sample.mp4"
        page = BrowserPage(
            "https://www.douyin.com/video/7336481666707229992",
            f'<script type="application/json">{{"play_addr":"{media_url}"}}</script>',
            request_headers={"Referer": "https://www.douyin.com/video/7336481666707229992", "User-Agent": "browser-UA"},
        )
        downloader = _MediaDownloader()
        adapter = DouyinCrawlerAdapter(
            fetcher=_Fetcher(page),
            browser_renderer=_Renderer(BrowserPage("", "")),
            media_downloader=downloader,
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "douyin.mp4"
            result = adapter.download(
                DownloadRequest(
                    url=page.url,
                    platform="douyin",
                    destination=destination,
                )
            )
            exists = destination.exists()

        self.assertEqual(result.status, AdapterStatus.SUCCESS)
        self.assertTrue(exists)
        self.assertEqual(result.mime_type, "video/mp4")
        self.assertEqual(downloader.calls[0]["page"].request_headers["User-Agent"], "browser-UA")
        self.assertEqual(result.provenance["methods_attempted"], ["public_detail"])

    def test_xiaohongshu_refreshes_expired_media_url_once(self) -> None:
        no_media = BrowserPage("https://www.xiaohongshu.com/explore/note", "<html></html>")
        first = BrowserPage(
            no_media.url,
            "<html></html>",
            media_urls=["https://sns-video-hw.xhscdn.com/expired.mp4"],
        )
        refreshed = BrowserPage(
            no_media.url,
            "<html></html>",
            media_urls=["https://sns-video-hw.xhscdn.com/fresh.mp4"],
        )
        renderer = _SequenceRenderer([first, refreshed])
        downloader = _MediaDownloader(failures=1)
        adapter = XiaohongshuCrawlerAdapter(
            fetcher=_Fetcher(no_media),
            browser_renderer=renderer,
            media_downloader=downloader,
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = adapter.download(
                DownloadRequest(
                    url=no_media.url,
                    platform="xiaohongshu",
                    destination=Path(tmp) / "xhs.mp4",
                )
            )

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertTrue(result.provenance["refresh_used"])
        self.assertEqual(len(renderer.calls), 2)
        self.assertEqual(len(downloader.calls), 2)

    def test_authorized_download_uses_ephemeral_page_cookies_without_serializing_them(self) -> None:
        detail_url = "https://www.douyin.com/video/7336481666707229992"
        challenge = BrowserPage(detail_url, "captcha")
        authorized = BrowserPage(
            detail_url,
            "<html></html>",
            media_urls=["https://v3-dy-o.zjcdn.com/video/tos/auth.mp4"],
            authorization_used=True,
            cookies=[{"name": "sessionid", "value": "secret-cookie"}],
        )
        renderer = _Renderer(challenge, authorized)
        downloader = _MediaDownloader()
        adapter = DouyinCrawlerAdapter(
            fetcher=_Fetcher(challenge),
            browser_renderer=renderer,
            auth_broker=_AuthBroker(),
            media_downloader=downloader,
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = adapter.download(
                DownloadRequest(
                    url=detail_url,
                    platform="douyin",
                    destination=Path(tmp) / "authorized.mp4",
                    auth_profile="Default",
                )
            )

        self.assertEqual(result.status, AdapterStatus.PARTIAL)
        self.assertTrue(downloader.calls[0]["page"].authorization_used)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertNotIn("secret-cookie", serialized)
        self.assertNotIn("sessionid", serialized)

    def test_download_rejects_non_video_mime_or_header(self) -> None:
        page = BrowserPage(
            "https://www.xiaohongshu.com/explore/note",
            "<html></html>",
            media_urls=["https://sns-video-hw.xhscdn.com/not-video.mp4"],
        )
        adapter = XiaohongshuCrawlerAdapter(
            fetcher=_Fetcher(page),
            browser_renderer=_Renderer(page),
            media_downloader=_MediaDownloader(mime_type="text/html", valid_header=False),
            minimum_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "invalid.mp4"
            result = adapter.download(
                DownloadRequest(
                    url=page.url,
                    platform="xiaohongshu",
                    destination=destination,
                )
            )
            exists = destination.exists()

        self.assertEqual(result.status, AdapterStatus.ERROR)
        self.assertFalse(exists)
        self.assertIn("extractor_broken", {error.code.value for error in result.errors})

    def test_registry_routes_download_and_skips_platforms_without_download_contract(self) -> None:
        registry = SourceAdapterRegistry()
        registry.register(BilibiliAdapter(search_callable=lambda args: "[]"))

        result = registry.download(
            DownloadRequest(
                url="https://www.bilibili.com/video/BV1",
                platform="bilibili",
                destination="unused.mp4",
            )
        )

        self.assertEqual(result.status, AdapterStatus.SKIPPED)

    def test_youtube_auto_mode_skips_when_network_probe_fails_and_caches_probe(self) -> None:
        calls = 0

        def offline(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise urllib.error.URLError("blocked")

        probe = YouTubeNetworkProbe(opener=offline)
        searches: list[dict[str, Any]] = []
        adapter = ConditionalYouTubeAdapter(
            mode="auto",
            probe=probe,
            search_callable=lambda args: searches.append(args),
        )

        first = adapter.search(SearchRequest(query="campus", platform="youtube"))
        second = adapter.search(SearchRequest(query="campus", platform="youtube"))

        self.assertEqual(first.status, AdapterStatus.SKIPPED)
        self.assertEqual(second.status, AdapterStatus.SKIPPED)
        self.assertEqual(calls, 1)
        self.assertEqual(searches, [])

    def test_youtube_on_mode_bypasses_probe_and_uses_existing_search_contract(self) -> None:
        adapter = ConditionalYouTubeAdapter(
            mode="on",
            search_callable=lambda args: json.dumps(
                [{"id": "abc", "title": "Campus", "url": "https://youtu.be/abc", "duration": 30}]
            ),
        )

        result = adapter.search(SearchRequest(query="campus", platform="youtube", limit=2))

        self.assertEqual(result.status, AdapterStatus.SUCCESS)
        self.assertEqual(result.candidates[0]["source"], "youtube")

    def test_bilibili_wrapper_normalizes_existing_search_contract(self) -> None:
        adapter = BilibiliAdapter(
            search_callable=lambda args: json.dumps(
                [{"bvid": "BV123", "title": "校园", "url": "https://www.bilibili.com/video/BV123", "duration": 20}]
            )
        )

        result = adapter.search(SearchRequest(query="校园", platform="bilibili"))

        self.assertEqual(result.status, AdapterStatus.SUCCESS)
        self.assertEqual(result.candidates[0]["id"], "BV123")

    def test_registry_blocks_undeclared_capabilities_by_default(self) -> None:
        class RiskyAdapter:
            name = "risky"
            platform = "risky"
            capabilities = frozenset({"captcha_bypass"})

            def search(self, request: SearchRequest):  # pragma: no cover
                raise AssertionError

        registry = SourceAdapterRegistry()
        with self.assertRaises(AdapterPolicyError):
            registry.register(RiskyAdapter())

    def test_default_registry_exposes_all_planned_platforms(self) -> None:
        registry = build_default_registry(browser_renderer=_Renderer(BrowserPage("", "")))

        self.assertEqual(
            registry.platforms(),
            ("bilibili", "youtube", "douyin", "xiaohongshu"),
        )


if __name__ == "__main__":
    unittest.main()
