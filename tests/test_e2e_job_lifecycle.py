"""任务生命周期 E2E：创建 → 查看 → 取消 →（服务重启 → interrupted）→ 恢复 → 再取消。

用法（默认使用本仓库的服务器重启脚本，也可通过环境变量 LIFECYCLE_RESTART_CMD 覆盖）：
    python tests/test_e2e_job_lifecycle.py --base-url http://8.161.229.68 --byok-key sk-...
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def wait_job_status(page, task_hint: str, statuses: set[str], timeout_ms: int = 120_000) -> dict:
    """Python 侧轮询 /jobs，直到标题含 task_hint 的任务进入目标状态；超时输出诊断。"""
    import time

    js = """async (hint) => {
        const r = await fetch('/jobs', { credentials: 'same-origin' });
        if (!r.ok) return { http: r.status };
        const payload = await r.json();
        const items = Array.isArray(payload.items) ? payload.items : [];
        const job = items.find((j) => (j.task || j.title || '').includes(hint));
        return job || null;
    }"""
    deadline = time.monotonic() + timeout_ms / 1000
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate(js, task_hint)
        if isinstance(last, dict) and last.get("status") in statuses:
            return last
        page.wait_for_timeout(3000)
    pills = page.locator(".status-pill").all_inner_texts()
    print(f"[diag] status pills on page: {pills}")
    print(f"[diag] last /jobs poll for '{task_hint}': {last}")
    raise TimeoutError(f"job '{task_hint}' did not reach {statuses} within {timeout_ms}ms")


def click_action_with_retry(page, button_text: str, task_hint: str, target: str, attempts: int = 3) -> dict:
    """点击动作按钮并等待状态翻转；nginx 限流（60r/m + burst）可能 503 拒绝动作请求，重试。

    前端会以 toast 暴露失败但不会自动重试，这里模拟用户再次点击。
    """
    for attempt in range(1, attempts + 1):
        page.click(f"button:has-text('{button_text}')")
        try:
            return wait_job_status(page, task_hint, {target}, timeout_ms=40_000)
        except TimeoutError:
            print(f"[warn] '{button_text}' attempt {attempt} did not reach {target} (可能被限流 503)，重试")
    raise TimeoutError(f"action '{button_text}' failed after {attempts} attempts")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://8.161.229.68")
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots-lifecycle")
    parser.add_argument("--byok-key", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"life_{uuid.uuid4().hex[:8]}"
    password = "LifePass123!"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.on("console", lambda msg: print(f"[console:{msg.type}] {msg.text}") if msg.type in ("error", "warning") else None)
        page.on("response", lambda resp: print(f"[http {resp.status}] {resp.request.method} {resp.url}") if resp.status >= 400 else None)

        # 0. 注册 + 配置“我的 API”（真实 Agent 模式需要）
        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", password)
        page.fill("#reg-confirm-password", password)
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)
        page.click("text=API 与运行设置")
        page.wait_for_selector("text=API 来源", timeout=10000)
        page.locator(".settings-workflow-option", has_text="我的 API").click()
        page.fill("input[type=password] >> nth=0", args.byok_key)
        page.click("button:has-text('保存')")
        page.wait_for_selector("text=我的 API 配置已保存", timeout=10000)
        page.locator(".settings-modal header button").click()
        page.wait_for_selector(".settings-modal", state="hidden", timeout=5000)
        print("[PASS] 00 registered + BYOK saved")

        # 1. 创建任务（agent 模式）
        page.locator(".composer-shell textarea").first.fill("生命周期测试：剪一个 10 秒水獭介绍短片")
        page.locator(".mode-selector-trigger").click()
        page.locator(".mode-selector-option", has_text="真实 Agent").click()
        page.locator(".composer-shell button").last.click()
        page.wait_for_selector(".status-pill", timeout=30000)
        page.screenshot(path=str(shots / "01-job-created.png"), full_page=True)
        print("[PASS] 01 job created (agent mode)")

        # 2. 查看：任务历史里能看到该任务并打开详情
        page.click("text=任务历史")
        page.wait_for_selector(".job-select-row:has-text('生命周期测试')", timeout=15000)
        page.screenshot(path=str(shots / "02-job-in-history.png"), full_page=True)
        page.locator(".job-select-row", has_text="生命周期测试").first.click()
        page.wait_for_selector("button:has-text('查看工作台')", timeout=15000)
        page.screenshot(path=str(shots / "03-job-detail.png"), full_page=True)
        print("[PASS] 02/03 job visible in history and detail opens")

        # 3. 取消运行中的任务（详情面板“先停止任务”，无确认弹窗）
        wait_job_status(page, "生命周期测试", {"running"})
        click_action_with_retry(page, "先停止任务", "生命周期测试", "cancelled")
        page.wait_for_selector(".status-pill:has-text('已停止')", timeout=30000)
        page.screenshot(path=str(shots / "04-job-cancelled.png"), full_page=True)
        print("[PASS] 04 running job cancelled via UI")

        # 4. 再建一个任务，等 running 后由外部重启后端 → interrupted → 恢复
        page.click("text=创作工作台")
        page.locator(".composer-shell textarea").first.fill("生命周期恢复测试：剪一个 10 秒水獭介绍短片")
        page.locator(".mode-selector-trigger").click()
        page.locator(".mode-selector-option", has_text="真实 Agent").click()
        page.locator(".composer-shell button").last.click()
        # 注意：workbench 在已选中任务时不渲染 .status-pill（TaskHero variant=sidebar），
        # 状态以 API 轮询为准，截图记录日志流界面。
        job2 = wait_job_status(page, "生命周期恢复测试", {"running"})
        print(f"[info] second job running: {job2.get('job_id')}")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(shots / "05-second-job-running.png"), full_page=True)
        print("[PASS] 05 second job running; restarting backend now")

        restart_cmd = os.environ.get("LIFECYCLE_RESTART_CMD", "")
        if restart_cmd:
            subprocess.run(restart_cmd, shell=True, check=True)
        else:
            subprocess.run([sys.executable, "tests/_restart_server_backend.py"], check=True)

        page.reload(wait_until="domcontentloaded")
        # 等应用恢复登录态与首轮数据加载，再切到任务历史
        page.wait_for_selector(f"text={username}", timeout=60000)
        page.click("text=任务历史")
        page.wait_for_selector(".job-select-row:has-text('生命周期恢复测试')", timeout=60000)
        # 后端启动时把 running 任务标记为 interrupted（“可恢复”），轮询等待状态翻转
        wait_job_status(page, "生命周期恢复测试", {"interrupted"}, timeout_ms=120_000)
        page.locator(".job-select-row", has_text="生命周期恢复测试").first.click()
        page.wait_for_selector(".status-pill:has-text('可恢复')", timeout=60000)
        page.screenshot(path=str(shots / "06-job-interrupted-after-restart.png"), full_page=True)
        print("[PASS] 06 job shows interrupted (可恢复) after backend restart")

        click_action_with_retry(page, "继续任务", "生命周期恢复测试", "running")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(shots / "07a-after-resume-click.png"), full_page=True)
        page.wait_for_selector(".status-pill.status-running", timeout=30000)
        page.screenshot(path=str(shots / "07-job-resumed-running.png"), full_page=True)
        print("[PASS] 07 interrupted job resumed via UI")

        # 5. 收尾：取消恢复后的任务，控制 API 成本
        click_action_with_retry(page, "先停止任务", "生命周期恢复测试", "cancelled")
        page.wait_for_selector(".status-pill:has-text('已停止')", timeout=30000)
        page.screenshot(path=str(shots / "08-resumed-job-cancelled.png"), full_page=True)
        print("[PASS] 08 resumed job cancelled (cost control)")

        browser.close()

    print(f"\nAll lifecycle E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
