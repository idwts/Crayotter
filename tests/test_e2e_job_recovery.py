"""失败任务恢复选择 E2E：failed 任务显示「从断点继续 / 重新开始」两条路径。

用无效 BYOK key 让 planner 快速失败（低成本），分别验证：
- 从断点继续 → 任务重新 queued/running
- 重新开始（带确认弹窗）→ 任务重新 queued/running
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def wait_job_status(page, task_hint: str, statuses: set[str], timeout_ms: int = 120_000) -> dict:
    js = """async (hint) => {
        const r = await fetch('/jobs', { credentials: 'same-origin' });
        if (!r.ok) return { http: r.status };
        const payload = await r.json();
        const items = Array.isArray(payload.items) ? payload.items : [];
        return items.find((j) => (j.task || j.title || '').includes(hint)) || null;
    }"""
    deadline = time.monotonic() + timeout_ms / 1000
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate(js, task_hint)
        if isinstance(last, dict) and last.get("status") in statuses:
            return last
        page.wait_for_timeout(3000)
    raise TimeoutError(f"job '{task_hint}' did not reach {statuses}; last={last}")


def open_job_detail(page, hint: str) -> None:
    page.click("text=任务历史")
    page.wait_for_selector(f".job-select-row:has-text('{hint}')", timeout=30000)
    page.locator(".job-select-row", has_text=hint).first.click()
    page.wait_for_selector("button:has-text('查看工作台')", timeout=15000)


def create_doomed_job(page, task: str) -> None:
    page.click("text=创作工作台")
    page.locator(".composer-shell textarea").first.fill(task)
    page.locator(".mode-selector-trigger").click()
    page.locator(".mode-selector-option", has_text="真实 Agent").click()
    page.locator(".composer-shell button").last.click()
    page.wait_for_selector(".composer-shell", timeout=10000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://8.161.229.68")
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots-recovery")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"rec_{uuid.uuid4().hex[:8]}"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        context.add_init_script("localStorage.setItem('crayotter.onboardingDone.v1', '1')")
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))

        # 0. 注册 + 保存无效 BYOK key（planner 会快速 401 失败）
        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", "RecPass123!")
        page.fill("#reg-confirm-password", "RecPass123!")
        page.check("#reg-agree-terms")
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)
        page.click("text=API 与运行设置")
        page.wait_for_selector("text=API 来源", timeout=10000)
        page.locator(".settings-workflow-option", has_text="我的 API").click()
        page.fill("input[type=password] >> nth=0", "sk-invalid-recovery-test-key")
        page.click("button:has-text('保存')")
        page.wait_for_selector("text=我的 API 配置已保存", timeout=10000)
        page.locator(".settings-modal header button").click()
        page.wait_for_selector(".settings-modal", state="hidden", timeout=5000)
        print("[PASS] 00 registered + invalid BYOK saved")

        # 1. 任务失败 → 详情出现两个恢复按钮 → 从断点继续
        create_doomed_job(page, "恢复测试A：剪一个 10 秒水獭短片")
        wait_job_status(page, "恢复测试A", {"failed"})
        open_job_detail(page, "恢复测试A")
        page.wait_for_selector("button:has-text('从断点继续')", timeout=15000)
        page.wait_for_selector("button:has-text('重新开始')", timeout=15000)
        page.screenshot(path=str(shots / "01-failed-job-actions.png"), full_page=True)
        print("[PASS] 01 failed job shows both recovery actions")

        page.click("button:has-text('从断点继续')")
        wait_job_status(page, "恢复测试A", {"queued", "running"})
        page.screenshot(path=str(shots / "02-resume-from-checkpoint.png"), full_page=True)
        print("[PASS] 02 resume-from-checkpoint requeues the job")
        wait_job_status(page, "恢复测试A", {"running", "failed"})
        page.click("button:has-text('先停止任务')") if page.locator("button:has-text('先停止任务')").count() else None
        wait_job_status(page, "恢复测试A", {"cancelled", "failed"}, timeout_ms=60_000)
        print("[PASS] 03 first job settled (cost control)")

        # 2. 第二个失败任务 → 重新开始（确认弹窗）
        create_doomed_job(page, "恢复测试B：剪一个 10 秒水獭短片")
        wait_job_status(page, "恢复测试B", {"failed"})
        open_job_detail(page, "恢复测试B")
        page.click("button:has-text('重新开始')")
        page.wait_for_selector(".confirm-dialog, [role=dialog]", timeout=10000)
        page.screenshot(path=str(shots / "03-restart-confirm.png"), full_page=True)
        page.locator("button:has-text('重新开始')").last.click()  # 确认弹窗中的确认按钮
        wait_job_status(page, "恢复测试B", {"queued", "running"})
        page.screenshot(path=str(shots / "04-restarted-running.png"), full_page=True)
        print("[PASS] 04 restart requeues the job with confirmation")
        wait_job_status(page, "恢复测试B", {"running", "failed"})
        page.click("button:has-text('先停止任务')") if page.locator("button:has-text('先停止任务')").count() else None
        wait_job_status(page, "恢复测试B", {"cancelled", "failed"}, timeout_ms=60_000)
        print("[PASS] 05 second job settled (cost control)")

        browser.close()

    print(f"\nAll recovery E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
