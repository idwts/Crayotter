"""密保问题 E2E：注册设置密保 → 忘记密码走密保找回 → 新密码登录。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://8.161.229.68"
SHOTS = Path("docs/worklogs/e2e-shots-security-question")


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    username = f"sqe_{uuid.uuid4().hex[:8]}"
    question = "我的高中班主任姓什么？"
    answer = "王"
    new_password = "SqNewPass88"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
        page.goto(f"{BASE}/ui/")
        page.wait_for_timeout(2000)

        # 1. 进入注册页
        page.click('text=立即注册')
        page.wait_for_timeout(800)
        page.fill("#reg-username", username)
        page.fill("#reg-password", "Sq12345678")
        page.fill("#reg-confirm-password", "Sq12345678")
        page.fill("#reg-security-question", question)
        page.fill("#reg-security-answer", answer)
        page.screenshot(path=str(SHOTS / "01-register-form.png"))
        page.click('button:has-text("注册")')
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "02-register-success.png"))
        assert page.locator('text=进入工作台').count() > 0, "注册成功页未出现"
        print("[PASS] 01 register with security question")

        # 2. 登出回登录页
        page.click('button:has-text("进入工作台")')
        page.wait_for_timeout(2500)
        page.click('button:has-text("退出登录")')
        page.wait_for_timeout(2000)
        assert page.locator("#password").count() > 0, "未回到登录页"
        print("[PASS] 02 logout to login page")

        # 3. 忘记密码 → 密保问题找回
        page.click('text=忘记密码')
        page.wait_for_timeout(800)
        page.click('button:has-text("密保问题找回")')
        page.fill("#reset-username", username)
        page.click("#reset-username")  # 触发 blur 前先聚焦其他元素
        page.click("h1")  # blur username
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "03-question-loaded.png"))
        assert page.locator(f'text={question}').count() > 0, "密保问题未显示"
        print("[PASS] 03 security question loaded by username")

        page.fill("#reset-security-answer", answer)
        page.fill("#reset-new-password", new_password)
        page.fill("#reset-confirm-password", new_password)
        page.click('button:has-text("重置密码")')
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "04-reset-done.png"))
        assert page.locator("#password").count() > 0, "重置后未回登录页"
        print("[PASS] 04 reset via security answer")

        # 4. 新密码登录
        page.fill("#username", username)
        page.fill("#password", new_password)
        page.click('button:has-text("登录")')
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOTS / "05-logged-in.png"))
        assert page.locator('.nav-item:has-text("创作工作台")').count() > 0, "新密码登录失败"
        print("[PASS] 05 login with new password")

        browser.close()
    print("\nAll security-question E2E tests passed. Screenshots saved to", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
