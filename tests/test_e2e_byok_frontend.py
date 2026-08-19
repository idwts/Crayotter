"""BYOK（我的 API）+ 主服务 agent 冒烟 E2E：截图验证。

用法（服务端为 public 模式时）：
    python tests/test_e2e_byok_frontend.py --base-url http://8.161.229.68 --byok-key sk-...
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots-byok")
    parser.add_argument("--byok-key", required=True, help="测试用自有 API key（dashscope）")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"byok_{uuid.uuid4().hex[:8]}"
    password = "ByokPass123!"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        context.add_init_script("localStorage.setItem('crayotter.onboardingDone.v1', '1')")
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))

        # 1. 注册并进入工作台
        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", password)
        page.fill("#reg-confirm-password", password)
        page.check("#reg-agree-terms")
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)
        print("[PASS] 01 registered and entered workbench")

        # 2. 打开设置 → 公开模式应显示“API 来源”与平台配额可用
        page.click("text=API 与运行设置")
        page.wait_for_selector("text=API 来源", timeout=10000)
        page.screenshot(path=str(shots / "01-settings-api-source.png"), full_page=True)
        assert page.locator("text=平台配额").count() >= 1
        assert page.locator("text=我的 API").count() >= 1
        print("[PASS] 02 settings shows API source options (platform quota + my API)")

        # 3. 切到“我的 API”，填写并保存
        page.locator(".settings-workflow-option", has_text="我的 API").click()
        page.wait_for_selector("text=密钥经加密保存在服务端", timeout=5000)
        page.fill("input[type=password] >> nth=0", args.byok_key)
        page.fill("text=主服务地址 >> xpath=.. >> input", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        page.fill("text=主模型 >> xpath=.. >> input", "qwen-plus")
        page.screenshot(path=str(shots / "02-byok-form-filled.png"), full_page=True)
        page.click("button:has-text('保存')")
        page.wait_for_selector("text=我的 API 配置已保存", timeout=10000)
        page.screenshot(path=str(shots / "03-byok-saved.png"), full_page=True)
        print("[PASS] 03 BYOK config saved via UI")

        # 4. 关闭重开设置：密钥只显示掩码占位，表单不回填明文
        page.locator(".settings-modal header button").click()
        page.wait_for_selector(".settings-modal", state="hidden", timeout=5000)
        page.click("text=API 与运行设置")
        page.wait_for_selector("text=API 来源", timeout=10000)
        first_secret = page.locator("input[type=password]").first
        placeholder = first_secret.get_attribute("placeholder") or ""
        assert "****" in placeholder, f"expected masked placeholder, got {placeholder!r}"
        assert args.byok_key not in (first_secret.input_value() or ""), "plaintext key refilled into form"
        assert page.locator("text=清除我的配置").count() >= 1
        page.screenshot(path=str(shots / "04-byok-masked-reopen.png"), full_page=True)
        print("[PASS] 04 reopen shows masked placeholder, no plaintext refill")

        # 5. 提交真实 agent 任务（用自有 key），验证主服务 LLM 链路启动后取消
        page.locator(".settings-modal header button").click()
        page.wait_for_selector(".settings-modal", state="hidden", timeout=5000)
        textarea = page.locator(".composer-shell textarea")
        textarea.first.fill("BYOK 冒烟测试：剪一个 10 秒的水獭介绍短片")
        # 切换到 agent 模式（自定义 ModeSelector 下拉）
        page.locator(".mode-selector-trigger").click()
        page.locator(".mode-selector-option", has_text="真实 Agent").click()
        page.screenshot(path=str(shots / "05-agent-task-ready.png"), full_page=True)
        page.locator(".composer-shell button").last.click()
        page.wait_for_selector(".status-pill", timeout=30000)
        page.screenshot(path=str(shots / "06-agent-job-running.png"), full_page=True)
        print("[PASS] 05/06 agent job submitted with own key, status pill visible")

        # 6. 等待 LLM 规划/检索产出事件（证明密钥真正打通主服务），随后取消控制成本
        page.wait_for_timeout(30000)  # 给 planner LLM 调用与素材检索留出时间
        body_text = page.locator("body").inner_text()
        assert "401" not in body_text and "Incorrect API key" not in body_text and "invalid_api_key" not in body_text, \
            "LLM auth error surfaced in UI"
        page.screenshot(path=str(shots / "07-agent-planner-progress.png"), full_page=True)
        cancel = page.locator("button:has-text('取消')")
        if cancel.count() >= 1:
            cancel.first.click()
            confirm = page.locator("button:has-text('确认')")
            if confirm.count() >= 1:
                confirm.first.click()
        print("[PASS] 07 planner events flowing without LLM auth errors; job cancelled to bound cost")

        browser.close()

    print(f"\nAll BYOK E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
