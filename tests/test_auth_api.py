"""后端认证接口功能与冲突测试。

用法:
    CRAYOTTER_DATABASE_URL=postgresql://... python tests/test_auth_api.py
    # 或服务在运行时:
    python tests/test_auth_api.py --base-url http://localhost:8765
"""
from __future__ import annotations

import argparse
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
import json


def request(
    base: str,
    method: str,
    path: str,
    body: dict | None = None,
    headers: dict | None = None,
    jar: CookieJar | None = None,
) -> tuple[int, dict]:
    url = base.rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    jar = jar if jar is not None else CookieJar()
    try:
        with build_opener(HTTPCookieProcessor(jar)).open(req) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(payload) if payload.strip() else {}
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        return exc.code, json.loads(payload) if payload.strip() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    base = args.base_url

    print(f"Testing against {base}")

    # 1. health 冲突测试
    status, data = request(base, "GET", "/health")
    assert status == 200 and data.get("ok") is True, f"health failed: {status} {data}"
    print("[PASS] /health still works")

    # 2. 匿名 session 仍然能访问 config（冲突测试）
    status, data = request(base, "GET", "/config")
    assert status == 200, f"config failed: {status} {data}"
    print("[PASS] /config accessible without auth")

    # 3. 注册
    import uuid
    username = f"tester_{uuid.uuid4().hex[:8]}"
    password = "TestPass123!"
    status, data = request(base, "POST", "/api/auth/register", {"username": username, "password": password})
    assert status == 201, f"register failed: {status} {data}"
    assert "user" in data and "recovery_codes" in data, f"register response malformed: {data}"
    print(f"[PASS] /api/auth/register created user {username}")

    # 4. 重复注册应失败
    status, data = request(base, "POST", "/api/auth/register", {"username": username, "password": password})
    assert status == 400, f"duplicate register should fail: {status} {data}"
    print("[PASS] duplicate register rejected")

    # 5. 登录（使用 cookie jar 保持 session）
    jar = CookieJar()
    status, login_data = request(base, "POST", "/api/auth/login", {"username": username, "password": password}, jar=jar)
    assert status == 200, f"login failed: {status} {login_data}"
    assert login_data["user"]["username"] == username
    print("[PASS] /api/auth/login succeeded")

    # 检查 cookie 存在
    auth_cookies = [c for c in jar if c.name == "crayotter_auth_session"]
    assert auth_cookies, "auth session cookie not set"
    print("[PASS] crayotter_auth_session cookie set")

    # 6. /api/auth/me
    status, me_data = request(base, "GET", "/api/auth/me", jar=jar)
    assert status == 200 and me_data["user"]["username"] == username, f"me failed: {me_data}"
    print("[PASS] /api/auth/me returns current user")

    # 7. 未授权访问 /api/auth/me
    status, data = request(base, "GET", "/api/auth/me")
    assert status == 401, f"unauthorized me should 401: {status} {data}"
    print("[PASS] /api/auth/me 401 without cookie")

    # 8. 改密码
    new_password = "NewPass456!"
    status, data = request(base, "POST", "/api/auth/password", {"old_password": password, "new_password": new_password}, jar=jar)
    assert status == 200, f"change password failed: {status} {data}"
    print("[PASS] /api/auth/password changed")

    # 9. 旧密码登录应失败，新密码成功
    status, data = request(base, "POST", "/api/auth/login", {"username": username, "password": password})
    assert status == 400, f"old password should fail: {status} {data}"
    status, data = request(base, "POST", "/api/auth/login", {"username": username, "password": new_password})
    assert status == 200, f"new password login failed: {status} {data}"
    print("[PASS] password change invalidated old password")

    # 10. 登出
    status, data = request(base, "POST", "/api/auth/logout", jar=jar)
    assert status == 200, f"logout failed: {status} {data}"
    print("[PASS] /api/auth/logout succeeded")

    print("\nAll tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
