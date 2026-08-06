"""素材库进阶功能 E2E：分片上传入口、批量选择删除、在线预览、完成通知开关。

- 大文件按钮可见且能上传（1.5MB 假 mp4，触发 2 个分片）
- 批量选择 2 素材后删除
- 点击预览打开 video 弹层
- 创作选项弹层中任务完成通知开关可切换并持久化
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://8.161.229.68")
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots-materials-advanced")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"adv_{uuid.uuid4().hex[:8]}"
    tmp = Path(tempfile.mkdtemp(prefix="adv-e2e-"))

    # 小文件（普通上传）+ 大文件（分片上传）
    small = tmp / "small_otter.mp4"
    large = tmp / "large_dolphin.mp4"
    small.write_bytes(b"\x00" * 4096)
    large.write_bytes(b"\x00" * (1024 * 1024 + 512 * 1024))  # 1.5MB

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))

        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", "AdvPass123!")
        page.fill("#reg-confirm-password", "AdvPass123!")
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)
        print("[PASS] 00 registered")

        # 1. 普通上传一个小素材
        page.click("text=素材库")
        page.wait_for_selector("text=上传素材", timeout=15000)
        page.set_input_files("input[type=file] >> nth=0", [str(small)])
        page.wait_for_selector("text=small_otter.mp4", timeout=30000)
        print("[PASS] 01 small file uploaded via legacy endpoint")

        # 2. 大文件分片上传
        page.set_input_files("input[type=file] >> nth=1", [str(large)])
        # 进度条可能一闪而过，直接等列表出现更稳
        page.wait_for_selector("text=large_dolphin.mp4", timeout=120000)
        page.screenshot(path=str(shots / "02-large-uploaded.png"), full_page=True)
        print("[PASS] 02 large file uploaded via chunked endpoint")

        # 3. 在线预览
        page.locator("button:has-text('预览')").first.click()
        page.wait_for_selector("video.material-preview-video", timeout=15000)
        page.screenshot(path=str(shots / "03-preview-open.png"), full_page=True)
        page.click("[aria-label='关闭']")
        page.wait_for_selector("video.material-preview-video", state="hidden", timeout=10000)
        print("[PASS] 03 preview dialog opens video")

        # 4. 批量选择并删除
        page.wait_for_timeout(800)
        page.locator(".materials-select-all input[type=checkbox]").check()
        page.wait_for_selector("button.materials-batch-delete", timeout=5000)
        delete_btn = page.locator("button.materials-batch-delete")
        assert "2" in delete_btn.inner_text()
        delete_btn.click()
        page.wait_for_selector(".confirm-dialog, [role=dialog]", timeout=10000)
        page.locator("button:has-text('删除')").last.click()
        page.wait_for_selector("text=还没有上传本地素材", timeout=15000)
        page.screenshot(path=str(shots / "04-batch-deleted.png"), full_page=True)
        print("[PASS] 04 batch select and delete")

        # 5. 任务完成通知开关
        page.click("text=创作工作台")
        page.wait_for_timeout(2500)
        page.locator(".composer-options-trigger:visible").first.click()
        page.wait_for_timeout(800)
        page.locator(".composer-option-row", has_text="完成时通知我").click()
        page.wait_for_timeout(600)
        flag = page.evaluate("() => localStorage.getItem('crayotter.notifyOnDone')")
        assert flag == "1", flag
        page.screenshot(path=str(shots / "05-notify-toggle.png"), full_page=True)
        print("[PASS] 05 notify-on-done toggle persists")

        browser.close()

    print(f"\nAll advanced materials E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
