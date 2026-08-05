"""素材库 上传/列表/条件搜索/删除 后端功能测试（对活服务器，注册隔离账号）。

覆盖：multipart 上传、GET /uploads 的 q 子串过滤、has_analysis 筛选、
sort/order 排序、DELETE 删除。使用无内容假 .mp4（上传不做转码校验）。
"""
from __future__ import annotations

import argparse
import uuid

import requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://8.161.229.68")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    username = f"upl_{uuid.uuid4().hex[:8]}"
    s = requests.Session()
    r = s.post(f"{base}/api/auth/register", json={"username": username, "password": "UplPass123!"}, timeout=30)
    assert r.status_code in (200, 201), r.text[:200]

    # 1. 上传两个素材（海豚/水獭，大小不同）
    for name, size in [("dolphin_show.mp4", 4096), ("otter_cute.mp4", 1024)]:
        r = s.post(
            f"{base}/uploads",
            files={"file": (name, b"\x00" * size, "video/mp4")},
            timeout=60,
        )
        assert r.status_code in (200, 201), f"upload {name}: {r.status_code} {r.text[:200]}"
    print("[PASS] 01 two materials uploaded")

    r = s.get(f"{base}/uploads", timeout=30)
    items = r.json()["items"]
    assert len(items) == 2, f"expect 2 items, got {len(items)}"
    print("[PASS] 02 list returns both uploads")

    # 2. q 子串过滤（大小写不敏感）
    items = s.get(f"{base}/uploads", params={"q": "OTTER"}, timeout=30).json()["items"]
    assert [i["name"] for i in items] == ["otter_cute.mp4"], items
    items = s.get(f"{base}/uploads", params={"q": "不存在"}, timeout=30).json()["items"]
    assert items == [], items
    print("[PASS] 03 q substring filter (case-insensitive + empty result)")

    # 3. has_analysis 筛选（新上传均无分析文件）
    items = s.get(f"{base}/uploads", params={"has_analysis": "1"}, timeout=30).json()["items"]
    assert items == [], items
    items = s.get(f"{base}/uploads", params={"has_analysis": "0"}, timeout=30).json()["items"]
    assert len(items) == 2, items
    print("[PASS] 04 has_analysis filter")

    # 4. 排序：按大小升序、按名称
    items = s.get(f"{base}/uploads", params={"sort": "size_bytes", "order": "asc"}, timeout=30).json()["items"]
    assert [i["name"] for i in items] == ["otter_cute.mp4", "dolphin_show.mp4"], items
    items = s.get(f"{base}/uploads", params={"sort": "name", "order": "asc"}, timeout=30).json()["items"]
    assert [i["name"] for i in items] == ["dolphin_show.mp4", "otter_cute.mp4"], items
    print("[PASS] 05 sort by size asc / name asc")

    # 5. 组合条件
    items = s.get(f"{base}/uploads", params={"q": "o", "sort": "size_bytes", "order": "desc"}, timeout=30).json()["items"]
    assert [i["name"] for i in items] == ["dolphin_show.mp4", "otter_cute.mp4"], items
    print("[PASS] 06 combined q + sort")

    # 6. 删除
    for item in items:
        r = s.delete(f"{base}/uploads", params={"path": item["display_path"]}, timeout=30)
        assert r.status_code == 200, f"delete: {r.status_code} {r.text[:200]}"
    items = s.get(f"{base}/uploads", timeout=30).json()["items"]
    assert items == [], items
    print("[PASS] 07 delete both materials")

    print("\nAll uploads search API tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
