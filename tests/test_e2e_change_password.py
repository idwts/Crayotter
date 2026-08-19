"""修改密码 E2E：设置弹窗「账号安全」页签全链路。

步骤：注册 → 打开设置 → 账号安全 → 改密 → 自动回登录页 → 旧密码失败 → 新密码登录进工作台。
运行：python tests/test_e2e_change_password.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import requests
requests.packages.urllib3.disable_warnings()  # 自签证书
_ORIG_SESSION_REQUEST = requests.Session.request
def _insecure_session_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _ORIG_SESSION_REQUEST(self, *args, **kwargs)
requests.Session.request = _insecure_session_request
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://8.161.229.68"
OLD_PW = "OldPass12345"
NEW_PW = "NewPass12345"
SHOTS = Path("docs/worklogs/e2e-shots-change-password")


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    username = f"pwui_{uuid.uuid4().hex[:8]}"
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/register", json={"username": username, "password": OLD_PW,
        "agree_terms": True}, timeout=15)
    assert r.status_code == 201, r.text
    cookies = s.cookies.get_dict()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 800})
        ctx.add_init_script("localStorage.setItem('crayotter.onboardingDone.v1', '1')")
        ctx.add_cookies([{"name": k, "value": v, "url": BASE} for k, v in cookies.items()])
        page = ctx.new_page()
        page.goto(BASE + "/ui/")
        page.wait_for_timeout(2500)
        print("[PASS] 00 registered & logged in")

        page.click('button:has-text("API 与运行设置")')
        page.wait_for_timeout(900)
        page.click('button:has-text("账号安全")')
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "01-account-section.png"))
        print("[PASS] 01 account security section visible")

        fields = page.locator(".settings-section input")
        fields.nth(0).fill(OLD_PW)
        fields.nth(1).fill(NEW_PW)
        fields.nth(2).fill(NEW_PW)
        page.click('button:has-text("修改密码")')
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOTS / "02-back-to-login.png"))
        assert page.locator('button:has-text("登录")').first.is_visible(), "改密后未回到登录页"
        print("[PASS] 02 password changed, back to login")

        # 旧密码登录应失败
        page.fill('input[placeholder*="用户名"]', username)
        page.fill('input[placeholder*="密码"]', OLD_PW)
        page.click('button:has-text("登录")')
        page.wait_for_timeout(1800)
        assert page.locator('text=用户名或密码错误').is_visible(), "旧密码登录未报错"
        page.screenshot(path=str(SHOTS / "03-old-password-rejected.png"))
        print("[PASS] 03 old password rejected")

        page.fill('input[placeholder*="密码"]', NEW_PW)
        page.click('button:has-text("登录")')
        page.wait_for_timeout(3000)
        assert page.locator('.nav-item:has-text("创作工作台")').is_visible(), "新密码登录未进工作台"
        page.screenshot(path=str(SHOTS / "04-new-password-works.png"))
        print("[PASS] 04 new password login works")

        browser.close()

    print("\nAll change-password E2E tests passed. Screenshots in", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
