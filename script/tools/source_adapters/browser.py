from __future__ import annotations

from typing import Any

from .base import AuthBroker
from .models import BrowserPage


class BrowserDependencyUnavailable(RuntimeError):
    pass


class OptionalPlaywrightRenderer:
    """Render a public page without making Playwright a runtime requirement.

    Authentication state is requested only for an explicit ``auth_profile`` and
    remains an in-memory object owned by the broker and Playwright context.
    """

    def __init__(self, *, headless: bool = True, browser_channel: str | None = None) -> None:
        self.headless = headless
        self.browser_channel = browser_channel

    def render(
        self,
        url: str,
        *,
        platform: str,
        timeout_seconds: float,
        auth_broker: AuthBroker | None = None,
        auth_profile: str = "",
    ) -> BrowserPage:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserDependencyUnavailable("Playwright is not installed") from exc

        storage_state: dict[str, Any] | None = None
        if auth_profile:
            if auth_broker is None:
                raise BrowserDependencyUnavailable("an auth broker is required for an authorized browser session")
            storage_state = auth_broker.storage_state(platform, auth_profile)
            if not storage_state:
                raise BrowserDependencyUnavailable("the requested browser auth profile is unavailable")

        media_urls: list[str] = []
        timeout_ms = int(max(1.0, min(float(timeout_seconds), 60.0)) * 1000)
        with sync_playwright() as playwright:
            launch_args: dict[str, Any] = {"headless": self.headless}
            if self.browser_channel:
                launch_args["channel"] = self.browser_channel
            browser = playwright.chromium.launch(**launch_args)
            try:
                context_args: dict[str, Any] = {}
                if storage_state is not None:
                    context_args["storage_state"] = storage_state
                context = browser.new_context(**context_args)
                page = context.new_page()

                def capture_response(response: Any) -> None:
                    try:
                        content_type = str(response.headers.get("content-type", "")).lower()
                        response_url = str(response.url)
                        if content_type.startswith("video/") or ".mp4" in response_url.lower():
                            media_urls.append(response_url)
                    except Exception:
                        return

                page.on("response", capture_response)
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
                except Exception:
                    pass
                html = page.content()
                final_url = page.url
                status_code = response.status if response is not None else None
                user_agent = str(page.evaluate("() => navigator.userAgent"))
                cookies = context.cookies()
                context.close()
            finally:
                browser.close()
        return BrowserPage(
            url=final_url,
            html=html,
            media_urls=list(dict.fromkeys(media_urls)),
            status_code=status_code,
            authorization_used=storage_state is not None,
            request_headers={"Referer": final_url, "User-Agent": user_agent},
            cookies=cookies,
        )
