"""前端自适应轮询 smoke：空闲（无 running/queued 任务）时 /jobs 轮询应降到 30s 档。

注册新用户后静置 45s，统计 GET /jobs 请求数：6s 档约 8 次，30s 档应 ≤3 次。
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://8.161.229.68")
    parser.add_argument("--observe-seconds", type=int, default=45)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    username = f"poll_{uuid.uuid4().hex[:8]}"
    jobs_hits: list[float] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page()
        page.on("request", lambda req: jobs_hits.append(time.monotonic()) if req.url.endswith("/jobs") and req.method == "GET" else None)

        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", "PollPass123!")
        page.fill("#reg-confirm-password", "PollPass123!")
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)

        start = time.monotonic()
        page.wait_for_timeout(args.observe_seconds * 1000)
        browser.close()

    idle_hits = [t for t in jobs_hits if t - jobs_hits[0] > 3] if jobs_hits else []
    print(f"GET /jobs total={len(jobs_hits)} idle-window={len(idle_hits)} in {args.observe_seconds}s")
    # 30s 档：45s 窗口内至多 3 次（首轮 + 30s + 60s 溢出容忍）；6s 档会有 7+
    assert len(idle_hits) <= 3, f"idle polling too frequent: {len(idle_hits)} hits"
    print("[PASS] idle polling is in the 30s cadence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
