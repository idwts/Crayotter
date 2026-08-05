"""素材库 E2E：上传 → 列表 → 条件搜索/筛选/排序 → 删除（UI 全链路截图）。"""
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
    parser.add_argument("--shots", default="docs/worklogs/e2e-shots-materials")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    username = f"mat_{uuid.uuid4().hex[:8]}"
    tmp = Path(tempfile.mkdtemp(prefix="mat-e2e-"))
    f1 = tmp / "dolphin_show.mp4"
    f2 = tmp / "otter_cute.mp4"
    f1.write_bytes(b"\x00" * 4096)
    f2.write_bytes(b"\x00" * 1024)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))

        page.goto(f"{base}/ui/", wait_until="domcontentloaded")
        page.click("text=立即注册")
        page.fill("#reg-username", username)
        page.fill("#reg-password", "MatPass123!")
        page.fill("#reg-confirm-password", "MatPass123!")
        page.click("button[type=submit]")
        page.wait_for_selector("text=注册成功", timeout=15000)
        page.click("text=进入工作台")
        page.wait_for_selector(f"text={username}", timeout=15000)
        print("[PASS] 00 registered")

        # 1. 素材库上传两个文件
        page.click("text=素材库")
        page.wait_for_selector("text=上传素材", timeout=15000)
        page.set_input_files("input[type=file]", [str(f1), str(f2)])
        page.wait_for_selector("text=dolphin_show.mp4", timeout=30000)
        page.wait_for_selector("text=otter_cute.mp4", timeout=30000)
        page.screenshot(path=str(shots / "01-uploads-listed.png"), full_page=True)
        print("[PASS] 01 two materials uploaded and listed")

        # 2. 名称搜索
        page.fill(".materials-search-input", "otter")
        page.wait_for_timeout(800)  # 防抖 300ms + 请求
        assert page.locator("text=otter_cute.mp4").count() == 1
        assert page.locator("text=dolphin_show.mp4").count() == 0
        page.screenshot(path=str(shots / "02-search-otter.png"), full_page=True)
        print("[PASS] 02 name search filters the list")

        # 3. 分析状态筛选（新素材均为“需首次分析”）
        page.fill(".materials-search-input", "")
        page.locator("select.materials-toolbar-select").first.select_option("yes")
        page.wait_for_timeout(800)
        assert page.locator("text=otter_cute.mp4").count() == 0
        page.screenshot(path=str(shots / "03-filter-analysed-empty.png"), full_page=True)
        page.locator("select.materials-toolbar-select").first.select_option("no")
        page.wait_for_timeout(800)
        assert page.locator("text=otter_cute.mp4").count() == 1
        print("[PASS] 03 has_analysis filter")

        # 4. 排序：按大小（desc → dolphin 在前）
        page.locator("select.materials-toolbar-select").nth(1).select_option("size_bytes")
        page.wait_for_timeout(800)
        rows = page.locator(".material-row strong").all_inner_texts()
        assert rows[0] == "dolphin_show.mp4", rows
        page.screenshot(path=str(shots / "04-sort-by-size.png"), full_page=True)
        print("[PASS] 04 sort by size desc")

        # 5. 删除（带确认弹窗）
        for _ in range(2):
            page.locator("button.text-action.danger", has_text="删除").first.click()
            page.wait_for_selector(".confirm-dialog, [role=dialog]", timeout=10000)
            page.locator("button:has-text('删除')").last.click()
            page.wait_for_timeout(1200)
        page.wait_for_selector("text=还没有上传本地素材", timeout=15000)
        page.screenshot(path=str(shots / "05-all-deleted.png"), full_page=True)
        print("[PASS] 05 both materials deleted via UI")

        browser.close()

    print(f"\nAll materials E2E tests passed. Screenshots saved to {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
