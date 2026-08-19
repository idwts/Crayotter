# -*- coding: utf-8 -*-
"""GET /files 属主访问控制线上 API 测试（public 模式）。

验证矩阵：
1. 属主可访问自己的上传素材（素材库预览/下载修复回归）
2. 其他用户访问该上传素材 → 403
3. 属主可访问自己任务产物
4. 其他用户访问该任务产物 → 403
5. 未上传过的全新会话访问他人上传 → 403（不依赖任务列表侧信道）

运行：python tests/test_files_owner_api.py
"""
import io
import secrets
import sys
import time

import requests
requests.packages.urllib3.disable_warnings()  # 自签证书
_ORIG_SESSION_REQUEST = requests.Session.request
def _insecure_session_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _ORIG_SESSION_REQUEST(self, *args, **kwargs)
requests.Session.request = _insecure_session_request

BASE = "https://8.161.229.68"

step = 0


def check(ok: bool, label: str) -> None:
    global step
    if not ok:
        print(f"[FAIL] {label}")
        sys.exit(1)
    step += 1
    print(f"[PASS] {step:02d} {label}")


def new_user(tag: str) -> requests.Session:
    s = requests.Session()
    name = f"files_{tag}_{secrets.token_hex(3)}"
    r = s.post(f"{BASE}/api/auth/register", json={"username": name, "password": "probePass123!",
        "agree_terms": True})
    assert r.status_code == 201, r.text
    return s


def main() -> None:
    A = new_user("A")
    B = new_user("B")

    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    r = A.post(f"{BASE}/uploads", files={"files": ("owner_a.mp4", io.BytesIO(mp4), "video/mp4")})
    check(r.status_code == 201, "A 上传素材")
    path_a = r.json()["items"][0]["path"]

    r = A.get(f"{BASE}/files", params={"path": path_a})
    check(r.status_code == 200, f"A 访问自己的上传素材 → 200（实际 {r.status_code}）")

    r = B.get(f"{BASE}/files", params={"path": path_a})
    check(r.status_code == 403, f"B 访问 A 的上传素材 → 403（实际 {r.status_code}）")

    r = A.post(f"{BASE}/jobs", json={
        "task": "files owner probe", "mode": "demo",
        "enable_phase2_research": False, "enable_plan_review": False,
        "direct_phase3_execution": True, "prefer_local_materials": True,
    })
    check(r.status_code == 201, "A 创建 demo 任务")
    job_id = r.json()["job_id"]

    art_path = None
    for _ in range(40):
        time.sleep(3)
        items = A.get(f"{BASE}/jobs/{job_id}/artifacts").json().get("items", [])
        if items:
            art_path = items[0]["path"]
            break
        if A.get(f"{BASE}/jobs/{job_id}").json().get("status") in ("completed", "failed"):
            break
    check(art_path is not None, "demo 任务产出至少一个产物")

    r = A.get(f"{BASE}/files", params={"path": art_path})
    check(r.status_code == 200, f"A 访问自己的任务产物 → 200（实际 {r.status_code}）")

    r = B.get(f"{BASE}/files", params={"path": art_path})
    check(r.status_code == 403, f"B 访问 A 的任务产物 → 403（实际 {r.status_code}）")

    # 任务目录租户化：产物路径应位于属主子目录下
    check(f"/jobs/{A.cookies.get('crayotter_session')}/" in art_path.replace("\\", "/"),
          f"新任务目录位于 JOBS_DIR/<owner>/ 下（{art_path}）")

    print(f"\n全部 {step} 步通过")


if __name__ == "__main__":
    main()
