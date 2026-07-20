from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parents[1] / "script" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from browser_auth import (  # noqa: E402
    BrowserAuthBroker,
    BrowserAuthRequest,
    filter_platform_cookies,
    redact_sensitive,
)


class BrowserAuthBrokerTests(unittest.TestCase):
    def test_filters_cookies_to_exact_platform_domain_suffixes(self) -> None:
        cookies = [
            {"name": "sessionid", "value": "douyin-secret", "domain": ".douyin.com"},
            {"name": "cdn", "value": "media-secret", "domain": "v.douyin.com"},
            {"name": "evil", "value": "evil-secret", "domain": "douyin.com.example.org"},
            {"name": "xhs", "value": "xhs-secret", "domain": ".xiaohongshu.com"},
        ]

        filtered = filter_platform_cookies("douyin", cookies)

        self.assertEqual([cookie["name"] for cookie in filtered], ["sessionid", "cdn"])
        self.assertNotIn("evil-secret", repr(filtered))
        self.assertNotIn("xhs-secret", repr(filtered))

    def test_authorization_result_contains_handle_but_no_cookie_secrets(self) -> None:
        secret = "very-sensitive-cookie-value"
        loader = lambda _request: [
            {"name": "web_session", "value": secret, "domain": ".xiaohongshu.com"},
            {"name": "unrelated", "value": "other-secret", "domain": ".example.com"},
        ]
        broker = BrowserAuthBroker(cookie_loader=loader)
        with tempfile.TemporaryDirectory() as temp_dir:
            request = BrowserAuthRequest(
                browser="chrome",
                profile="Default",
                platform="xiaohongshu",
                workspace=Path(temp_dir),
            )
            result = broker.authorize(request)

        self.assertTrue(result.authorized)
        self.assertEqual(result.cookie_count, 1)
        serialized = json.dumps(result.as_event(), ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("other-secret", serialized)
        self.assertNotIn("web_session", serialized)
        self.assertNotIn(result.session_handle, serialized)
        self.assertNotIn(result.session_handle, repr(result))

        state = broker.get_storage_state(result.session_handle)
        self.assertEqual(state["cookies"][0]["value"], secret)
        self.assertTrue(broker.release(result.session_handle))
        self.assertIsNone(broker.get_storage_state(result.session_handle))

    def test_loader_exception_is_sanitized(self) -> None:
        secret = "secret-embedded-in-browser-error"

        def broken_loader(_request):
            raise RuntimeError(secret)

        broker = BrowserAuthBroker(cookie_loader=broken_loader)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = broker.authorize(
                BrowserAuthRequest(
                    browser="chrome",
                    profile="Default",
                    platform="douyin",
                    workspace=Path(temp_dir),
                )
            )

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.reason, "profile_read_failed")
        self.assertNotIn(secret, repr(result))
        self.assertNotIn(secret, json.dumps(result.as_event()))

    def test_redacts_nested_credentials(self) -> None:
        payload = {
            "status": "failed",
            "cookie": "cookie-secret",
            "nested": {
                "authorization": "Bearer bearer-secret",
                "safe": "extractor_broken",
            },
            "items": [{"token": "token-secret", "reason": "expired"}],
        }

        redacted = redact_sensitive(payload)
        serialized = json.dumps(redacted)

        self.assertNotIn("cookie-secret", serialized)
        self.assertNotIn("bearer-secret", serialized)
        self.assertNotIn("token-secret", serialized)
        self.assertEqual(redacted["nested"]["safe"], "extractor_broken")

    def test_workspace_cleanup_only_removes_private_auth_directory(self) -> None:
        broker = BrowserAuthBroker()
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            keep = workspace / "artifact.txt"
            keep.write_text("keep", encoding="utf-8")
            auth_root = workspace / ".crayotter" / "browser_auth"
            state_file = auth_root / "douyin-session" / "Default" / "Cookies"
            state_file.parent.mkdir(parents=True)
            state_file.write_text("cookie-secret", encoding="utf-8")

            broker.cleanup_workspace(workspace)

            self.assertFalse(auth_root.exists())
            self.assertTrue(keep.exists())
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")

    def test_missing_profile_returns_structured_auth_required(self) -> None:
        broker = BrowserAuthBroker(cookie_loader=lambda _request: [])
        with tempfile.TemporaryDirectory() as temp_dir:
            result = broker.authorize(
                BrowserAuthRequest(
                    browser="chrome",
                    profile=None,
                    platform="douyin",
                    workspace=Path(temp_dir),
                )
            )

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.reason, "profile_required")
        self.assertEqual(result.allowed_domains, ("douyin.com", "iesdouyin.com"))

    def test_optional_playwright_dependency_fails_cleanly(self) -> None:
        broker = BrowserAuthBroker()
        original_import = __import__

        def controlled_import(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ModuleNotFoundError("playwright is intentionally absent")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "Default"
            profile.mkdir()
            request = BrowserAuthRequest(
                browser="chrome",
                profile=str(profile),
                platform="douyin",
                workspace=Path(temp_dir),
            )
            with patch("builtins.__import__", side_effect=controlled_import):
                result = broker.authorize(request)
                manual = broker.begin_manual_authorization(request)

        self.assertEqual(result.status, "auth_required")
        self.assertEqual(result.reason, "playwright_unavailable")
        self.assertEqual(manual.result.reason, "playwright_unavailable")
        self.assertIsNone(manual.session)


if __name__ == "__main__":
    unittest.main()
