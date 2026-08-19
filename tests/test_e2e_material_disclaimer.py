"""素材免责声明 E2E：素材库常驻提示条 + 创作选项弹层提示均可见（截图留证）。"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://8.161.229.68")
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots-disclaimer")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"dsc_{uuid.uuid4().hex[:8]}"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        context.add_init_script("localStorage.setItem('crayotter.onboardingDone.v1', '1')")
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))

        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", "DscPass123!")
        page.fill("#reg-confirm-password", "DscPass123!")
        page.check("#reg-agree-terms")
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)
        print("[PASS] 00 registered")

        # 1. 素材库常驻免责声明
        page.click("text=素材库")
        page.wait_for_selector(".materials-disclaimer", timeout=15000)
        text = page.locator(".materials-disclaimer").inner_text()
        assert "免责声明" in text and "敏感" in text, text
        page.screenshot(path=str(shots / "01-materials-disclaimer.png"), full_page=True)
        print("[PASS] 01 materials view shows disclaimer")

        # 2. 创作选项弹层中的素材免责提示
        page.click("text=创作工作台")
        page.wait_for_timeout(1500)  # 等视图切换与轮询渲染稳定，避免菜单节点被重建
        page.locator(".composer-options-trigger:visible").first.click()
        page.wait_for_selector(".composer-options-menu:visible", timeout=10000)
        page.wait_for_timeout(800)
        assert page.locator(".composer-material-disclaimer:visible").count() >= 1
        text = page.locator(".composer-options-menu:visible").first.inner_text()
        assert "敏感" in text, text
        page.screenshot(path=str(shots / "02-composer-attach-disclaimer.png"), full_page=True)
        print("[PASS] 02 composer options show attach disclaimer")

        # 3. 英文文案
        # 3. 英文文案（切语言会关掉弹层，改到素材库校验提示条）
        # 3. 英文文案：新 context 预置 localStorage 语言=en + API 注册注入会话，
        #    避免界面切换语言被服务端偏好回写造成的抖动
        import requests as http

        username_en = f"dse_{uuid.uuid4().hex[:8]}"
        session = http.Session()
        resp = session.post(f"{base}/api/auth/register", json={"username": username_en, "password": "DscPass123!",
        "agree_terms": True}, timeout=30)
        assert resp.status_code in (200, 201), resp.text[:200]
        host = base.split("://", 1)[-1].split(":")[0]
        cookies = [
            {"name": name, "value": value, "domain": host, "path": "/"}
            for name, value in session.cookies.items()
        ]
        assert any(c["name"] == "crayotter_session" for c in cookies), "register did not set session cookie"
        context_en = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        context_en.add_init_script("localStorage.setItem('crayotter.language', 'en')")
        context_en.add_cookies(cookies)
        page_en = context_en.new_page()
        page_en.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page_en.locator(".nav-item:has-text('Materials')").first.click()
        page_en.wait_for_selector(".materials-disclaimer", timeout=15000)
        text = page_en.locator(".materials-disclaimer").first.inner_text()
        assert "sensitive" in text.lower(), text
        page_en.screenshot(path=str(shots / "03-materials-disclaimer-en.png"), full_page=True)
        context_en.close()
        print("[PASS] 03 english disclaimer copy")

        browser.close()

    print(f"\nAll disclaimer E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
