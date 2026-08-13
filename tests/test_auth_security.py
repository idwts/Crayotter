"""认证防爆破与改密链路 API 测试（线上服务器）。

- 登录连续失败 5 次 → 第 6 次 429 + retry_after
- 锁定后即使密码正确也 429
- 恢复码重置连续失败触发同锁定器
- 改密全链路：改密→旧 session 失效→旧密码登录失败→新密码登录成功

运行：python tests/test_auth_security.py
"""
from __future__ import annotations

import sys
import uuid

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://8.161.229.68"
PASSWORD = "SecTest12345"


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        if cond:
            print(f"[PASS] {name} {extra}")
        else:
            failures += 1
            print(f"[FAIL] {name} {extra}")

    # --- 登录失败锁定 ---
    username = f"lock_{uuid.uuid4().hex[:8]}"
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/register", json={"username": username, "password": PASSWORD}, timeout=15)
    check("注册测试账号", r.status_code == 201, str(r.status_code))

    anon = requests.Session()  # 不带凭据的会话做爆破
    last = None
    for i in range(5):
        last = anon.post(f"{BASE}/api/auth/login", json={"username": username, "password": "wrong-pass-1"}, timeout=15)
    check("前 5 次错误登录返回 400", last is not None and last.status_code == 400, str(last.status_code if last else None))

    r = anon.post(f"{BASE}/api/auth/login", json={"username": username, "password": "wrong-pass-1"}, timeout=15)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("第 6 次登录 429 + retry_after", r.status_code == 429 and body.get("retry_after", 0) > 0, f"{r.status_code} retry_after={body.get('retry_after')}")

    r = anon.post(f"{BASE}/api/auth/login", json={"username": username, "password": PASSWORD}, timeout=15)
    check("锁定期间正确密码也 429", r.status_code == 429, str(r.status_code))

    # --- 恢复码重置共享锁定器（用新账号避免与上面锁定键冲突） ---
    username2 = f"lock_{uuid.uuid4().hex[:8]}"
    r = requests.post(f"{BASE}/api/auth/register", json={"username": username2, "password": PASSWORD}, timeout=15)
    check("注册第二个测试账号", r.status_code == 201, str(r.status_code))
    last = None
    for _ in range(5):
        last = anon.post(f"{BASE}/api/auth/reset", json={"username": username2, "recovery_code": "deadbeef", "new_password": "Whatever123"}, timeout=15)
    check("前 5 次错误重置返回 400", last is not None and last.status_code == 400, str(last.status_code if last else None))
    r = anon.post(f"{BASE}/api/auth/reset", json={"username": username2, "recovery_code": "deadbeef", "new_password": "Whatever123"}, timeout=15)
    check("第 6 次重置 429", r.status_code == 429, str(r.status_code))

    # --- 改密全链路 ---
    username3 = f"pw_{uuid.uuid4().hex[:8]}"
    s3 = requests.Session()
    r = s3.post(f"{BASE}/api/auth/register", json={"username": username3, "password": PASSWORD}, timeout=15)
    check("注册改密账号", r.status_code == 201, str(r.status_code))

    r = s3.post(f"{BASE}/api/auth/password", json={"old_password": "wrong-old-1", "new_password": "NewPass12345"}, timeout=15)
    check("原密码错误 → 400", r.status_code == 400, str(r.status_code))

    r = s3.post(f"{BASE}/api/auth/password", json={"old_password": PASSWORD, "new_password": "short"}, timeout=15)
    check("新密码过短 → 400", r.status_code == 400, str(r.status_code))

    r = s3.post(f"{BASE}/api/auth/password", json={"old_password": PASSWORD, "new_password": "NewPass12345"}, timeout=15)
    check("改密成功 → 200", r.status_code == 200, str(r.status_code))

    r = s3.get(f"{BASE}/api/auth/me", timeout=15)
    check("改密后旧 session 失效 → 401", r.status_code == 401, str(r.status_code))

    r = requests.post(f"{BASE}/api/auth/login", json={"username": username3, "password": PASSWORD}, timeout=15)
    check("旧密码登录 → 400", r.status_code == 400, str(r.status_code))

    r = requests.post(f"{BASE}/api/auth/login", json={"username": username3, "password": "NewPass12345"}, timeout=15)
    check("新密码登录 → 200", r.status_code == 200, str(r.status_code))

    print(f"\n== {'ALL PASS' if failures == 0 else f'{failures} FAILURES'} ==")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
