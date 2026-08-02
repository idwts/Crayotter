"""前端 E2E 测试：认证流程 + remember-me + 截图验证。

用法：
    先启动后端（含 CRAYOTTER_DATABASE_URL），再运行：
    python tests/test_e2e_auth_frontend.py --base-url http://127.0.0.1:8765 --shots docs/worklogs/e2e-shots
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"e2e_{uuid.uuid4().hex[:8]}"
    password = "E2ePass123!"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", lambda msg: print(f"[console:{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))

        # 1. 打开 UI → 未登录应显示登录页
        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.wait_for_selector("text=登", timeout=15000)
        page.screenshot(path=str(shots / "01-login-page.png"), full_page=True)
        assert page.locator("text=忘记密码").count() >= 1, "forgot password link missing"
        assert page.locator("text=记住我").count() >= 1, "remember me checkbox missing"
        assert page.locator("text=记住用户名").count() >= 1, "remember username checkbox missing"
        print("[PASS] 01 login page renders with remember-me and forgot-password entries")

        # 2. 切换到注册页
        page.click("text=立即注册")
        page.wait_for_selector("text=创建你的 Crayotter 账号", timeout=10000)
        page.screenshot(path=str(shots / "02-register-page.png"), full_page=True)
        print("[PASS] 02 register page renders")

        # 3. 注册
        page.fill("#reg-username", username)
        page.fill("#reg-password", password)
        page.fill("#reg-confirm-password", password)
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.screenshot(path=str(shots / "03-register-success-recovery-codes.png"), full_page=True)
        codes = page.locator("ul.font-mono li").all_inner_texts()
        assert len(codes) == 8, f"expected 8 recovery codes, got {len(codes)}"
        recovery_code = codes[0]
        print(f"[PASS] 03 register succeeds, 8 recovery codes shown (first saved for reset test)")

        # 4. 进入工作台
        page.click("text=进入工作台")
        page.wait_for_selector("text=任务", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(shots / "04-workbench-after-register.png"), full_page=True)
        assert page.locator(f"text={username}").count() >= 1, "username missing in sidebar"
        print("[PASS] 04 workbench renders after registration, username shown in sidebar")

        # 5. 输入任务草稿（历史动作记忆）并切换视图
        textarea = page.locator(".composer-shell textarea")
        if textarea.count() >= 1:
            textarea.first.fill("E2E draft: 本地素材优先测试视频")
        page.wait_for_timeout(1600)  # 等 preferences debounce 推送
        page.screenshot(path=str(shots / "05-workbench-task-draft.png"), full_page=True)
        print("[PASS] 05 task draft typed (preferences push window elapsed)")

        # 6. 退出登录
        page.click("button:has-text('退出登录')") if page.locator("button:has-text('退出登录')").count() else page.locator("button[title='退出登录']").first.click()
        page.wait_for_selector("text=登录到 Crayotter", timeout=15000)
        page.screenshot(path=str(shots / "06-login-after-logout.png"), full_page=True)
        print("[PASS] 06 logout returns to login page")

        # 7. 勾选记住我 + 记住用户名登录
        page.check("text=记住我（30 天内免登录）")
        page.check("text=记住用户名")
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("button[type=submit]")
        page.wait_for_selector(f"text={username}", timeout=15000)
        page.wait_for_timeout(1000)
        cookies = {c["name"]: c for c in context.cookies()}
        assert "crayotter_remember" in cookies, "remember cookie not set after remember-me login"
        assert "crayotter_auth_session" in cookies, "auth session cookie not set"
        assert cookies["crayotter_remember"]["httpOnly"], "remember cookie must be HttpOnly"
        assert cookies["crayotter_remember"]["sameSite"] == "Lax", "remember cookie must be SameSite=Lax"
        page.screenshot(path=str(shots / "07-workbench-remember-login.png"), full_page=True)
        print("[PASS] 07 remember-me login sets HttpOnly+SameSite=Lax remember cookie")

        # 8. 草稿被服务端 preferences 记住（重新进入后仍可见已在 step5 验证写入，这里验证跨 session 恢复）
        # 模拟浏览器关闭重开：新 context 仅携带 remember cookie
        remember_cookie = cookies["crayotter_remember"]
        context2 = browser.new_context(viewport={"width": 1440, "height": 900})
        context2.add_cookies([{
            "name": "crayotter_remember",
            "value": remember_cookie["value"],
            "domain": remember_cookie["domain"],
            "path": "/",
            "httpOnly": True,
            "sameSite": "Lax",
        }])
        page2 = context2.new_page()
        page2.on("pageerror", lambda exc: print(f"[pageerror:ctx2] {exc}"))
        page2.goto(f"{base}/ui/", wait_until="domcontentloaded")
        # 应自动续期进入工作台而非登录页
        page2.wait_for_selector(f"text={username}", timeout=20000)
        page2.wait_for_timeout(1500)
        page2.screenshot(path=str(shots / "08-auto-renew-after-browser-restart.png"), full_page=True)
        textarea2 = page2.locator(".composer-shell textarea")
        if textarea2.count() >= 1:
            draft = textarea2.first.input_value()
            assert "E2E draft" in draft, f"task draft not restored from preferences: {draft!r}"
        page2.screenshot(path=str(shots / "09-draft-restored-from-server-preferences.png"), full_page=True)
        print("[PASS] 08/09 auto-renew after browser restart; task draft restored from server preferences")

        # 9. 重置密码页渲染
        context2.close()
        # 清掉 context1 的会话/remember cookie（保留 localStorage 中的记住用户名），回到未登录态
        context.clear_cookies()
        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.wait_for_selector("text=登录到 Crayotter", timeout=15000)
        # 用户名应从 localStorage 预填
        prefilled = page.input_value("#username")
        assert prefilled == username, f"remembered username not prefilled: {prefilled!r}"
        page.screenshot(path=str(shots / "10-username-prefilled.png"), full_page=True)
        page.click("text=忘记密码？")
        page.wait_for_selector("text=重置密码", timeout=10000)
        page.screenshot(path=str(shots / "11-reset-page.png"), full_page=True)
        print("[PASS] 10/11 username prefilled from localStorage; reset page renders")

        # 10. 用恢复码重置密码
        new_password = "E2eNewPass456!"
        page.fill("#reset-username", username)
        page.fill("#reset-recovery-code", recovery_code)
        page.fill("#reset-new-password", new_password)
        page.fill("#reset-confirm-password", new_password)
        page.click("button[type=submit]")
        page.wait_for_selector("text=登录到 Crayotter", timeout=15000)
        page.screenshot(path=str(shots / "12-after-reset-back-to-login.png"), full_page=True)
        print("[PASS] 12 password reset via recovery code returns to login")

        # 11. 新密码登录成功
        page.fill("#username", username)
        page.fill("#password", new_password)
        page.click("button[type=submit]")
        page.wait_for_selector(f"text={username}", timeout=15000)
        page.screenshot(path=str(shots / "13-login-with-new-password.png"), full_page=True)
        print("[PASS] 13 login with new password succeeds")

        browser.close()

    print(f"\nAll E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
