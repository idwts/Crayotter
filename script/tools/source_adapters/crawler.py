from __future__ import annotations

import html as html_module
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base import AuthBroker, BrowserRenderer, MediaDownloader
from .browser import BrowserDependencyUnavailable, OptionalPlaywrightRenderer
from .models import (
    AUTHORIZATION_ERROR_CODES,
    AdapterError,
    AdapterErrorCode,
    AdapterStatus,
    BrowserPage,
    DownloadRequest,
    SearchRequest,
    SourceDownloadResult,
    SourceSearchResult,
)
from .normalization import merge_adapter_candidates, normalize_adapter_candidate

_JSON_SCRIPT_RE = re.compile(
    r"<script[^>]+(?:type=[\"']application/(?:ld\+)?json[\"']|id=[\"'][^\"']*(?:state|data)[^\"']*[\"'])[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_MEDIA_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+(?:\.mp4|video/tos)[^\s\"'<>\\]*", re.IGNORECASE)

_PLATFORM_PAGE_DOMAINS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "rednote.com"),
}
_PLATFORM_MEDIA_DOMAINS = {
    "douyin": (
        "douyin.com", "douyinvod.com", "bytecdn.cn", "zjcdn.com",
        "snssdk.com", "amemv.com", "ixigua.com", "byteimg.com",
    ),
    "xiaohongshu": (
        "xiaohongshu.com", "xhscdn.com", "xhscdn.net", "xhslink.com",
    ),
}


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(normalized == suffix or normalized.endswith("." + suffix) for suffix in suffixes)


def _validate_platform_url(url: str, platform: str, *, media: bool = False) -> str:
    parsed = urllib.parse.urlsplit(str(url or ""))
    host = (parsed.hostname or "").rstrip(".").lower()
    allowed = (_PLATFORM_MEDIA_DOMAINS if media else _PLATFORM_PAGE_DOMAINS).get(platform, ())
    if parsed.scheme != "https" or not host or not _host_matches(host, allowed):
        raise ValueError(f"untrusted {platform} {'media' if media else 'page'} URL")
    return url


def _cookie_header_for_url(cookies: list[dict[str, Any]], url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    request_path = parsed.path or "/"
    pairs: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "").lstrip(".").rstrip(".").lower()
        cookie_path = str(cookie.get("path") or "/")
        if not name or not domain or not _host_matches(host, (domain,)):
            continue
        if not request_path.startswith(cookie_path.rstrip("/") + "/") and request_path != cookie_path:
            if cookie_path != "/":
                continue
        if cookie.get("secure") and parsed.scheme != "https":
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


class PublicPageFetcher:
    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self._opener = opener or urllib.request.urlopen

    def fetch(self, url: str, *, timeout_seconds: float) -> BrowserPage:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
        )
        response = self._opener(request, timeout=max(1.0, min(float(timeout_seconds), 20.0)))
        try:
            payload = response.read(4 * 1024 * 1024)
            charset = "utf-8"
            headers = getattr(response, "headers", None)
            if headers is not None:
                get_charset = getattr(headers, "get_content_charset", None)
                if callable(get_charset):
                    charset = get_charset() or charset
            body = payload.decode(charset, errors="replace")
            final_url = str(getattr(response, "url", "") or getattr(response, "geturl", lambda: url)())
            status = int(getattr(response, "status", 200) or 200)
            return BrowserPage(
                url=final_url,
                html=body,
                status_code=status,
                request_headers={
                    "Referer": final_url,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                },
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


class MediaDownloadFailure(RuntimeError):
    def __init__(self, code: AdapterErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class EphemeralSessionDownloader:
    """HTTP downloader carrying the detail page's ephemeral session context."""

    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self._opener = opener or urllib.request.urlopen

    def download(
        self,
        media_url: str,
        destination: Path,
        *,
        page: BrowserPage,
        platform: str,
        timeout_seconds: float,
    ) -> tuple[str, int]:
        _validate_platform_url(media_url, platform, media=True)
        headers = {
            "Referer": page.request_headers.get("Referer") or page.url,
            "User-Agent": page.request_headers.get("User-Agent") or "Mozilla/5.0 Crayotter/1.0",
            "Accept": "video/*,*/*;q=0.8",
        }
        cookie_header = _cookie_header_for_url(page.cookies, media_url)
        if cookie_header:
            headers["Cookie"] = cookie_header
        request = urllib.request.Request(media_url, headers=headers)
        partial = destination.with_name(destination.name + ".part")
        try:
            response = self._opener(request, timeout=max(1.0, min(float(timeout_seconds), 120.0)))
            try:
                status = int(getattr(response, "status", 200) or 200)
                if status in {401, 403, 410}:
                    raise MediaDownloadFailure(
                        AdapterErrorCode.MEDIA_URL_FORBIDDEN,
                        f"media URL returned HTTP {status}",
                        retryable=True,
                    )
                mime_type = str(getattr(response, "headers", {}).get("Content-Type", "") or "").split(";", 1)[0].lower()
                declared_length_text = str(getattr(response, "headers", {}).get("Content-Length", "") or "").strip()
                try:
                    declared_length = int(declared_length_text) if declared_length_text else None
                except ValueError:
                    declared_length = None
                destination.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with partial.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        size += len(chunk)
                if declared_length is not None and declared_length != size:
                    raise MediaDownloadFailure(
                        AdapterErrorCode.EXTRACTOR_BROKEN,
                        f"media response length mismatch: expected {declared_length}, received {size}",
                        retryable=True,
                    )
                os.replace(partial, destination)
                return mime_type, size
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except urllib.error.HTTPError as exc:
            code = AdapterErrorCode.MEDIA_URL_FORBIDDEN if exc.code in {401, 403, 410} else AdapterErrorCode.INTERNAL_ERROR
            raise MediaDownloadFailure(code, f"media request returned HTTP {exc.code}", retryable=True) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MediaDownloadFailure(AdapterErrorCode.TIMEOUT, str(exc), retryable=True) from exc
        finally:
            if partial.exists():
                partial.unlink()


class _CrawlerAdapter:
    capabilities = frozenset(
        {
            "public_page_crawl",
            "rendered_dom_extraction",
            "browser_network_observation",
            "user_authorized_browser_session",
            "media_response_download",
        }
    )
    search_url_template = ""
    platform = ""
    name = ""

    def __init__(
        self,
        *,
        fetcher: PublicPageFetcher | None = None,
        browser_renderer: BrowserRenderer | None = None,
        auth_broker: AuthBroker | None = None,
        media_downloader: MediaDownloader | None = None,
        minimum_interval_seconds: float = 0.75,
    ) -> None:
        self.fetcher = fetcher or PublicPageFetcher()
        self.browser_renderer = browser_renderer
        self.auth_broker = auth_broker
        self.media_downloader = media_downloader or EphemeralSessionDownloader()
        self.minimum_interval_seconds = max(0.0, float(minimum_interval_seconds))
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def download(self, request: DownloadRequest) -> SourceDownloadResult:
        started = time.monotonic()
        destination = Path(request.destination).expanduser().resolve(strict=False)
        errors: list[AdapterError] = []
        methods: list[str] = []
        refresh_used = False

        try:
            _validate_platform_url(request.url, self.platform)
        except ValueError as exc:
            return SourceDownloadResult(
                platform=self.platform,
                status=AdapterStatus.ERROR,
                errors=[AdapterError(AdapterErrorCode.POLICY_BLOCKED, str(exc))],
                latency_seconds=time.monotonic() - started,
            )

        page = self._fetch_public(
            request.url,
            SearchRequest(
                query="",
                platform=self.platform,
                timeout_seconds=request.timeout_seconds,
                auth_profile=request.auth_profile,
            ),
            errors,
        )
        if page is not None:
            methods.append("public_detail")
            result = self._download_from_page(page, request, destination, errors)
            if result is not None:
                return self._download_result(started, destination, result, errors, methods, refresh_used)

        renderer = self.browser_renderer or OptionalPlaywrightRenderer()
        anonymous_page = self._render_detail(request, renderer, errors, authorized=False)
        if anonymous_page is not None:
            methods.append("anonymous_browser_detail")
            result = self._download_from_page(anonymous_page, request, destination, errors)
            if result is not None:
                return self._download_result(started, destination, result, errors, methods, refresh_used)

            if _has_refreshable_media_error(errors):
                refresh_used = True
                refreshed = self._render_detail(request, renderer, errors, authorized=False)
                if refreshed is not None:
                    methods.append("anonymous_browser_refresh")
                    result = self._download_from_page(refreshed, request, destination, errors)
                    if result is not None:
                        return self._download_result(started, destination, result, errors, methods, refresh_used)

        if request.auth_profile and self.auth_broker is not None:
            authorized_page = self._render_detail(request, renderer, errors, authorized=True)
            if authorized_page is not None:
                methods.append("authorized_browser_detail")
                result = self._download_from_page(authorized_page, request, destination, errors)
                if result is not None:
                    return self._download_result(started, destination, result, errors, methods, refresh_used)

        authorization_needed = any(
            error.code in AUTHORIZATION_ERROR_CODES or error.authorization_supported for error in errors
        )
        if not errors:
            errors.append(AdapterError(AdapterErrorCode.NOT_FOUND, "no playable media URL found"))
        return SourceDownloadResult(
            platform=self.platform,
            status=AdapterStatus.AUTHORIZATION_REQUIRED if authorization_needed else AdapterStatus.ERROR,
            errors=_dedupe_errors(errors),
            latency_seconds=time.monotonic() - started,
            provenance={"methods_attempted": methods, "refresh_used": refresh_used},
        )

    def _render_detail(
        self,
        request: DownloadRequest,
        renderer: BrowserRenderer,
        errors: list[AdapterError],
        *,
        authorized: bool,
    ) -> BrowserPage | None:
        search_request = SearchRequest(
            query="",
            platform=self.platform,
            timeout_seconds=request.timeout_seconds,
            auth_profile=request.auth_profile,
        )
        return self._render(request.url, search_request, renderer, errors, authorized=authorized)

    def _download_from_page(
        self,
        page: BrowserPage,
        request: DownloadRequest,
        destination: Path,
        errors: list[AdapterError],
    ) -> tuple[str, int] | None:
        challenge = _classify_page_error(page)
        if challenge is not None:
            errors.append(challenge)
            return None
        if request.user_agent:
            page.request_headers["User-Agent"] = request.user_agent
        media_urls = _extract_media_urls(page)
        if not media_urls:
            errors.append(
                AdapterError(
                    AdapterErrorCode.EXTRACTOR_BROKEN,
                    "detail page contained no observable media URL",
                    retryable=True,
                    authorization_supported=True,
                )
            )
            return None
        for media_url in media_urls[:3]:
            try:
                _validate_platform_url(media_url, self.platform, media=True)
                mime_type, size = self.media_downloader.download(
                    media_url,
                    destination,
                    page=page,
                    platform=self.platform,
                    timeout_seconds=request.timeout_seconds,
                )
                validation_error = _validate_downloaded_video(destination, mime_type, size)
                if validation_error is None:
                    return mime_type, size
                errors.append(validation_error)
            except MediaDownloadFailure as exc:
                errors.append(AdapterError(exc.code, str(exc), retryable=exc.retryable, authorization_supported=exc.code == AdapterErrorCode.MEDIA_URL_FORBIDDEN))
            except ValueError:
                errors.append(
                    AdapterError(AdapterErrorCode.POLICY_BLOCKED, "untrusted media URL was rejected")
                )
            except Exception as exc:
                errors.append(AdapterError(AdapterErrorCode.INTERNAL_ERROR, str(exc), retryable=True))
        return None

    def _download_result(
        self,
        started: float,
        destination: Path,
        download: tuple[str, int],
        errors: list[AdapterError],
        methods: list[str],
        refresh_used: bool,
    ) -> SourceDownloadResult:
        mime_type, size = download
        return SourceDownloadResult(
            platform=self.platform,
            status=AdapterStatus.SUCCESS if not errors else AdapterStatus.PARTIAL,
            path=str(destination),
            mime_type=mime_type,
            size_bytes=size,
            errors=_dedupe_errors(errors),
            latency_seconds=time.monotonic() - started,
            provenance={"methods_attempted": methods, "refresh_used": refresh_used},
        )

    def search(self, request: SearchRequest) -> SourceSearchResult:
        started = time.monotonic()
        search_url = self.search_url_template.format(query=urllib.parse.quote(request.query))
        errors: list[AdapterError] = []
        methods: list[str] = []

        page = self._fetch_public(search_url, request, errors)
        if page is not None:
            methods.append("public_page")
            challenge = _classify_page_error(page)
            if challenge is not None:
                errors.append(challenge)
            else:
                candidates = self._extract(page, request)
                if candidates:
                    return self._result(started, candidates, errors, methods)

        renderer = self.browser_renderer
        if renderer is None:
            renderer = OptionalPlaywrightRenderer()
        anonymous_page = self._render(search_url, request, renderer, errors, authorized=False)
        if anonymous_page is not None:
            methods.append("anonymous_browser")
            challenge = _classify_page_error(anonymous_page)
            if challenge is not None:
                errors.append(challenge)
            else:
                candidates = self._extract(anonymous_page, request)
                if candidates:
                    return self._result(started, candidates, errors, methods)

        needs_authorization = any(error.code in AUTHORIZATION_ERROR_CODES for error in errors)
        if request.auth_profile and self.auth_broker is not None:
            authorized_page = self._render(search_url, request, renderer, errors, authorized=True)
            if authorized_page is not None:
                methods.append("authorized_browser")
                challenge = _classify_page_error(authorized_page)
                if challenge is not None:
                    errors.append(challenge)
                else:
                    candidates = self._extract(authorized_page, request)
                    if candidates:
                        return self._result(started, candidates, errors, methods)

        if needs_authorization or any(error.authorization_supported for error in errors):
            status = AdapterStatus.AUTHORIZATION_REQUIRED
        else:
            status = AdapterStatus.ERROR
            if not errors:
                errors.append(AdapterError(AdapterErrorCode.NOT_FOUND, f"no {self.platform} candidates found"))
            elif all(error.code == AdapterErrorCode.DEPENDENCY_UNAVAILABLE for error in errors):
                # A missing optional browser is not a crash; public extraction simply produced no candidates.
                errors.append(AdapterError(AdapterErrorCode.NOT_FOUND, f"no public {self.platform} candidates found"))
        return SourceSearchResult(
            platform=self.platform,
            status=status,
            errors=_dedupe_errors(errors),
            latency_seconds=time.monotonic() - started,
            provenance={"methods_attempted": methods, "search_url": search_url},
        )

    def _fetch_public(
        self,
        url: str,
        request: SearchRequest,
        errors: list[AdapterError],
    ) -> BrowserPage | None:
        try:
            with self._request_lock:
                delay = self.minimum_interval_seconds - (time.monotonic() - self._last_request_at)
                if delay > 0:
                    time.sleep(delay)
                page = self.fetcher.fetch(url, timeout_seconds=request.timeout_seconds)
                _validate_platform_url(page.url, self.platform)
                self._last_request_at = time.monotonic()
                return page
        except urllib.error.HTTPError as exc:
            errors.append(_http_error(exc.code))
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(AdapterError(AdapterErrorCode.TIMEOUT, str(exc), retryable=True))
        except Exception as exc:
            errors.append(AdapterError(AdapterErrorCode.INTERNAL_ERROR, str(exc), retryable=True))
        return None

    def _render(
        self,
        url: str,
        request: SearchRequest,
        renderer: BrowserRenderer,
        errors: list[AdapterError],
        *,
        authorized: bool,
    ) -> BrowserPage | None:
        try:
            page = renderer.render(
                url,
                platform=self.platform,
                timeout_seconds=request.timeout_seconds,
                auth_broker=self.auth_broker if authorized else None,
                auth_profile=request.auth_profile if authorized else "",
            )
            _validate_platform_url(page.url, self.platform)
            return page
        except BrowserDependencyUnavailable as exc:
            code = AdapterErrorCode.PROFILE_UNAVAILABLE if authorized else AdapterErrorCode.DEPENDENCY_UNAVAILABLE
            errors.append(AdapterError(code, str(exc)))
        except TimeoutError as exc:
            errors.append(AdapterError(AdapterErrorCode.TIMEOUT, str(exc), retryable=True))
        except Exception as exc:
            errors.append(AdapterError(AdapterErrorCode.EXTRACTOR_BROKEN, str(exc), retryable=True))
        return None

    def _extract(self, page: BrowserPage, request: SearchRequest) -> list[dict[str, Any]]:
        raw_candidates = self.extract_candidates(page.html, page.url)
        normalized = [
            normalize_adapter_candidate(candidate, platform=self.platform, query=request.query)
            for candidate in raw_candidates
        ]
        return merge_adapter_candidates(normalized)[: request.bounded_limit()]

    def extract_candidates(self, html: str, page_url: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _result(
        self,
        started: float,
        candidates: list[dict[str, Any]],
        errors: list[AdapterError],
        methods: list[str],
    ) -> SourceSearchResult:
        return SourceSearchResult(
            platform=self.platform,
            status=AdapterStatus.SUCCESS if not errors else AdapterStatus.PARTIAL,
            candidates=candidates,
            errors=_dedupe_errors(errors),
            latency_seconds=time.monotonic() - started,
            provenance={"methods_attempted": methods},
        )


class DouyinCrawlerAdapter(_CrawlerAdapter):
    name = "douyin"
    platform = "douyin"
    search_url_template = "https://www.douyin.com/search/{query}?type=video"

    def extract_candidates(self, html: str, page_url: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for node in _iter_json_dicts(html):
            aweme_id = str(node.get("aweme_id") or node.get("item_id") or "").strip()
            if not aweme_id or not aweme_id.isdigit():
                continue
            candidate = dict(node)
            candidate["aweme_id"] = aweme_id
            candidate["url"] = str(node.get("share_url") or f"https://www.douyin.com/video/{aweme_id}")
            candidates.append(candidate)
        for aweme_id in re.findall(r"(?:douyin\.com)?/video/(\d{8,})", html):
            candidates.append({"aweme_id": aweme_id, "url": f"https://www.douyin.com/video/{aweme_id}"})
        return candidates


class XiaohongshuCrawlerAdapter(_CrawlerAdapter):
    name = "xiaohongshu"
    platform = "xiaohongshu"
    search_url_template = "https://www.xiaohongshu.com/search_result?keyword={query}&source=web_search_result_notes"

    def extract_candidates(self, html: str, page_url: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for node in _iter_json_dicts(html):
            note_card = node.get("note_card") if isinstance(node.get("note_card"), dict) else node
            note_id = str(
                node.get("note_id")
                or node.get("noteId")
                or (node.get("id") if any(key in note_card for key in ("display_title", "title", "video")) else "")
                or ""
            ).strip()
            if not re.fullmatch(r"[0-9a-fA-F]{16,32}", note_id):
                continue
            candidate = dict(note_card)
            candidate["note_id"] = note_id
            candidate["url"] = str(node.get("url") or f"https://www.xiaohongshu.com/explore/{note_id}")
            if not candidate.get("title"):
                candidate["title"] = candidate.get("display_title", "")
            candidates.append(candidate)
        pattern = r"/(?:explore|discovery/item)/([0-9a-fA-F]{16,32})"
        for note_id in re.findall(pattern, html):
            candidates.append({"note_id": note_id, "url": f"https://www.xiaohongshu.com/explore/{note_id}"})
        return candidates


def _iter_json_dicts(html: str) -> Iterator[dict[str, Any]]:
    documents: list[Any] = []
    unescaped = html_module.unescape(str(html or ""))
    for match in _JSON_SCRIPT_RE.finditer(unescaped):
        try:
            documents.append(json.loads(match.group(1).strip()))
        except (TypeError, ValueError):
            continue
    # Common SSR assignments are not application/json scripts. raw_decode avoids
    # executing page JavaScript and only accepts a JSON object/array literal.
    decoder = json.JSONDecoder()
    for marker in ("__INITIAL_STATE__", "__INITIAL_DATA__", "__NEXT_DATA__"):
        start = unescaped.find(marker)
        while start >= 0:
            object_start = min(
                [position for position in (unescaped.find("{", start), unescaped.find("[", start)) if position >= 0],
                default=-1,
            )
            if object_start >= 0:
                try:
                    value, _ = decoder.raw_decode(unescaped[object_start:])
                    documents.append(value)
                except ValueError:
                    pass
            start = unescaped.find(marker, start + len(marker))

    def walk(value: Any) -> Iterator[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from walk(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from walk(nested)

    for document in documents:
        yield from walk(document)


def _classify_page_error(page: BrowserPage) -> AdapterError | None:
    if page.status_code in {401, 403}:
        return _http_error(int(page.status_code))
    if page.status_code == 429:
        return _http_error(429)
    text = re.sub(r"\s+", " ", page.html or "").lower()
    markers = (
        (AdapterErrorCode.CAPTCHA_REQUIRED, ("captcha", "验证码", "安全验证")),
        (AdapterErrorCode.CHALLENGE_DETECTED, ("verifycenter", "访问过于频繁", "异常访问", "风险验证")),
    )
    for code, values in markers:
        if any(value in text for value in values):
            return AdapterError(code, f"{code.value} encountered", retryable=True, authorization_supported=True)
    return None


def _http_error(status: int) -> AdapterError:
    if status == 429:
        return AdapterError(
            AdapterErrorCode.RATE_LIMITED,
            "public page rate limited",
            retryable=True,
            authorization_supported=True,
            details={"status_code": status},
        )
    if status in {401, 403}:
        return AdapterError(
            AdapterErrorCode.AUTH_REQUIRED,
            "public page requires authorization",
            retryable=True,
            authorization_supported=True,
            details={"status_code": status},
        )
    return AdapterError(
        AdapterErrorCode.INTERNAL_ERROR,
        f"public page returned HTTP {status}",
        retryable=status >= 500,
        details={"status_code": status},
    )


def _dedupe_errors(errors: list[AdapterError]) -> list[AdapterError]:
    seen: set[tuple[str, str]] = set()
    result: list[AdapterError] = []
    for error in errors:
        identity = (error.code.value, error.message)
        if identity not in seen:
            seen.add(identity)
            result.append(error)
    return result


def _extract_media_urls(page: BrowserPage) -> list[str]:
    urls = list(page.media_urls)
    decoded = html_module.unescape(page.html or "").replace("\\u002F", "/").replace("\\/", "/")
    urls.extend(_MEDIA_URL_RE.findall(decoded))
    for node in _iter_json_dicts(decoded):
        for value in node.values():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                lowered = value.lower()
                if ".mp4" in lowered or "video/tos" in lowered or "sns-video" in lowered:
                    urls.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.startswith(("http://", "https://")):
                        lowered = item.lower()
                        if ".mp4" in lowered or "video/tos" in lowered or "sns-video" in lowered:
                            urls.append(item)
    return list(dict.fromkeys(urls))


def _validate_downloaded_video(destination: Path, mime_type: str, size: int) -> AdapterError | None:
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    allowed_mime = normalized_mime.startswith("video/") or normalized_mime in {
        "application/octet-stream",
        "binary/octet-stream",
    }
    try:
        actual_size = destination.stat().st_size
        with destination.open("rb") as downloaded:
            header = downloaded.read(16)
    except OSError as exc:
        return AdapterError(AdapterErrorCode.EXTRACTOR_BROKEN, f"download output is unavailable: {exc}")
    valid_header = (
        len(header) >= 12 and header[4:8] == b"ftyp"
        or header.startswith(b"\x1aE\xdf\xa3")
        or header.startswith(b"FLV")
        or header.startswith(b"OggS")
        or header.startswith(b"\x47")
    )
    if not allowed_mime or size <= 0 or actual_size <= 0 or not valid_header:
        try:
            destination.unlink()
        except OSError:
            pass
        return AdapterError(
            AdapterErrorCode.EXTRACTOR_BROKEN,
            "downloaded response failed MIME, size, or video file-header validation",
            retryable=True,
        )
    return None


def _has_refreshable_media_error(errors: list[AdapterError]) -> bool:
    return any(error.code in {AdapterErrorCode.MEDIA_URL_FORBIDDEN, AdapterErrorCode.TIMEOUT} for error in errors)
