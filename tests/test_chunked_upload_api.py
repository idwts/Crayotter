"""大文件分片上传后端功能测试（对活服务器，注册隔离账号）。

覆盖：init 创建会话、逐片上传、complete 合并、列表可见、删除。
"""
from __future__ import annotations

import argparse
import uuid

import requests
requests.packages.urllib3.disable_warnings()  # 自签证书
_ORIG_SESSION_REQUEST = requests.Session.request
def _insecure_session_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _ORIG_SESSION_REQUEST(self, *args, **kwargs)
requests.Session.request = _insecure_session_request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://8.161.229.68")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    username = f"chk_{uuid.uuid4().hex[:8]}"
    s = requests.Session()
    r = s.post(f"{base}/api/auth/register", json={"username": username, "password": "ChkPass123!",
        "agree_terms": True}, timeout=30)
    assert r.status_code in (200, 201), r.text[:200]

    # 故意造 2 个分片：服务器 chunk_size 默认 1MB，这里用 1.5MB 内容确保分 2 片
    total = b"\x00" * (1024 * 1024 + 512 * 1024)
    name = "chunked_clip.mp4"

    r = s.post(f"{base}/uploads/chunked/init", json={"name": name, "size_bytes": len(total)}, timeout=30)
    assert r.status_code == 201, f"init: {r.status_code} {r.text[:200]}"
    session = r.json()
    upload_id = session["upload_id"]
    chunk_size = session["chunk_size"]
    assert len(upload_id) == 32, upload_id
    print("[PASS] 01 chunked init")

    total_chunks = (len(total) + chunk_size - 1) // chunk_size
    for index in range(total_chunks):
        start = index * chunk_size
        end = min((index + 1) * chunk_size, len(total))
        blob = total[start:end]
        r = s.post(
            f"{base}/uploads/chunked/{upload_id}?index={index}",
            data=blob,
            headers={"Content-Type": "application/octet-stream"},
            timeout=60,
        )
        assert r.status_code == 200, f"chunk {index}: {r.status_code} {r.text[:200]}"
    print("[PASS] 02 chunks uploaded")

    r = s.post(f"{base}/uploads/chunked/{upload_id}/complete", timeout=120)
    assert r.status_code == 201, f"complete: {r.status_code} {r.text[:200]}"
    items = r.json()["items"]
    assert len(items) == 1, items
    assert items[0]["name"] == "chunked_clip.mp4", items[0]
    assert items[0]["size_bytes"] == len(total), items[0]
    print("[PASS] 03 complete merged")

    items = s.get(f"{base}/uploads", timeout=30).json()["items"]
    assert len(items) == 1, items
    display_path = items[0]["display_path"]

    r = s.delete(f"{base}/uploads", params={"path": display_path}, timeout=30)
    assert r.status_code == 200, f"delete: {r.status_code} {r.text[:200]}"
    assert s.get(f"{base}/uploads", timeout=30).json()["items"] == []
    print("[PASS] 04 list and delete")

    print("\nAll chunked upload API tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
