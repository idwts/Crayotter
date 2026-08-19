# -*- coding: utf-8 -*-
"""用户协议入口/同意勾选/使用引导/技术概览 E2E（线上 https）。

步骤：
1. 注册页不勾选协议直接提交 → 显示错误提示，未注册
2. 注册页打开《用户协议》→ 占位内容展示，返回注册页
3. 勾选协议注册成功 → 进入工作台自动弹出使用引导
4. 引导翻页到最后一步 → 「开始使用」关闭
5. 侧栏「使用引导」重新打开 → 跳过关闭
6. 侧栏「技术概览」→ 占位内容展示
7. 侧栏「《用户协议》」→ 应用内占位页

截图：docs/worklogs/e2e-shots-onboarding/
运行：python tests/test_e2e_onboarding.py
"""
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://8.161.229.68"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "worklogs" / "e2e-shots-onboarding"
SHOTS.mkdir(parents=True, exist_ok=True)

step = 0


def check(ok: bool, label: str) -> None:
    global step
    if not ok:
        print(f"[FAIL] {label}")
        sys.exit(1)
    step += 1
    print(f"[PASS] {step:02d} {label}")


def main() -> None:
    username = f"ob_{secrets.token_hex(3)}"
    password = "Ob12345678"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True).new_page()
        page.goto(f"{BASE}/ui/", wait_until="domcontentloaded")
        page.wait_for_selector("text=立即注册", timeout=15000)

        # 1. 注册页不勾选协议提交 → 前端校验拦截
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", password)
        page.fill("#reg-confirm-password", password)
        page.click('button:has-text("注册")')
        page.wait_for_selector("text=请先阅读并同意用户协议", timeout=5000)
        page.screenshot(path=str(SHOTS / "01-agree-required.png"))
        check(True, "未勾选协议提交被拦截并提示")

        # 2. 打开用户协议占位页并返回
        page.click("text=《用户协议》")
        page.wait_for_selector("text=协议内容开发中", timeout=5000)
        page.screenshot(path=str(SHOTS / "02-agreement-page.png"))
        check(True, "用户协议占位页展示")
        page.click('button:has-text("返回")')
        page.wait_for_selector("#reg-username", timeout=5000)
        check(True, "返回注册页")
        page.fill("#reg-username", username)
        page.fill("#reg-password", password)
        page.fill("#reg-confirm-password", password)

        # 3. 勾选协议注册 → 进入工作台自动弹出引导
        page.check("#reg-agree-terms")
        page.click('button:has-text("注册")')
        page.wait_for_selector('button:has-text("进入工作台")', timeout=10000)
        page.click('button:has-text("进入工作台")')
        page.wait_for_selector("text=欢迎使用 Crayotter", timeout=10000)
        page.wait_for_timeout(500)  # 等入场动画结束再截图
        page.screenshot(path=str(SHOTS / "03-onboarding-step1.png"))
        check(True, "首次进入自动弹出使用引导")

        # 4. 翻页到最后 → 开始使用
        for _ in range(4):
            page.click('button:has-text("下一步")')
        page.wait_for_selector('button:has-text("开始使用")', timeout=5000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / "04-onboarding-last.png"))
        page.click('button:has-text("开始使用")')
        page.wait_for_selector("text=欢迎使用 Crayotter", state="detached", timeout=5000)
        check(True, "引导完成并关闭")

        # 5. 侧栏重开引导 → 跳过
        page.click('button:has-text("使用引导")')
        page.wait_for_selector("text=欢迎使用 Crayotter", timeout=5000)
        page.click('button:has-text("跳过")')
        page.wait_for_selector("text=欢迎使用 Crayotter", state="detached", timeout=5000)
        check(True, "侧栏重开引导并跳过")

        # 6. 技术概览占位页
        page.click('button:has-text("技术概览")')
        page.wait_for_selector("text=技术概览开发中", timeout=5000)
        page.screenshot(path=str(SHOTS / "05-tech-overview.png"))
        check(True, "技术概览占位页展示")
        page.click('button:has-text("返回")')

        # 7. 应用内用户协议占位页
        page.click('button:has-text("《用户协议》")')
        page.wait_for_selector("text=协议内容开发中", timeout=5000)
        page.screenshot(path=str(SHOTS / "06-agreement-inapp.png"))
        check(True, "应用内用户协议占位页展示")

        browser.close()
    print(f"\n全部 {step} 步通过，截图位于 {SHOTS}")


if __name__ == "__main__":
    main()
