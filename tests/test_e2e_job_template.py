"""任务模板/一键复跑 E2E：历史任务详情点击「用作模板」后，创作面板套用原任务参数。"""
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

BASE = "https://8.161.229.68"
SHOTS = Path("docs/worklogs/e2e-shots-job-template")
TASK_TEXT = "demo template validation job"


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    username = f"tpl_{uuid.uuid4().hex[:8]}"
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/register", json={"username": username, "password": "Tpl123456",
        "agree_terms": True}, timeout=15)
    assert r.status_code == 201, r.text
    r = s.post(f"{BASE}/jobs", json={
        "task": TASK_TEXT, "mode": "demo",
        "enable_phase2_research": False, "enable_plan_review": True,
        "direct_phase3_execution": False, "prefer_local_materials": True,
    }, timeout=15)
    assert r.status_code == 201, r.text
    job = r.json()
    print("[PASS] 00 demo job created:", job["job_id"])
    cookies = s.cookies.get_dict()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 800})
        ctx.add_init_script("localStorage.setItem('crayotter.onboardingDone.v1', '1')")
        for k, v in cookies.items():
            ctx.add_cookies([{"name": k, "value": v, "url": BASE}])
        page = ctx.new_page()
        page.goto(f"{BASE}/ui/")
        page.wait_for_timeout(2500)

        page.click('.nav-item:has-text("任务历史")')
        page.wait_for_timeout(1200)
        # demo 模式 + ASCII 演示任务在 UI 中显示为本地化标题「演示任务」
        page.click('.job-select-row:has-text("演示任务")')
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "01-job-detail.png"))
        print("[PASS] 01 job detail opened")

        page.click('button:has-text("用作模板")')
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "02-template-applied.png"))

        value = page.input_value(".composer-shell textarea")
        assert value == TASK_TEXT, f"textarea = {value!r}"
        print("[PASS] 02 task text applied to composer")

        # 验证创作选项套用：phase2=False（未勾选）、本地素材优先=True（勾选）
        page.click(".composer-options-trigger:visible")
        page.wait_for_timeout(800)
        menu = page.locator(".composer-options-menu:visible")
        phase2_checked = menu.locator(".composer-option-row", has_text="Phase 2").locator("svg.lucide-check").count() > 0
        local_checked = menu.locator(".composer-option-row", has_text="本地素材优先").locator("svg.lucide-check").count() > 0
        page.screenshot(path=str(SHOTS / "03-options-applied.png"))
        assert not phase2_checked, "Phase 2 应为未勾选"
        assert local_checked, "本地素材优先应为勾选"
        print("[PASS] 03 workflow options applied (phase2 off, local-first on)")

        browser.close()
    print("\nAll job-template E2E tests passed. Screenshots saved to", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
