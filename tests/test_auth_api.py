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


def get_cookie_value(jar: CookieJar, name: str) -> str | None:
    for cookie in jar:
        if cookie.name == name:
            return cookie.value
    return None


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
    recovery_code = data["recovery_codes"][0]
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

    # 11. 恢复码重置密码：短密码应被拒绝
    status, data = request(base, "POST", "/api/auth/reset", {
        "username": username, "recovery_code": recovery_code, "new_password": "short",
    })
    assert status == 400, f"short new password should be rejected: {status} {data}"
    print("[PASS] /api/auth/reset rejects short new password")

    # 12. 恢复码重置密码：成功
    reset_password = "ResetPass789!"
    status, data = request(base, "POST", "/api/auth/reset", {
        "username": username, "recovery_code": recovery_code, "new_password": reset_password,
    })
    assert status == 200, f"reset by recovery code failed: {status} {data}"
    print("[PASS] /api/auth/reset succeeded with recovery code")

    # 13. 旧密码（改后的）失效，重置密码可登录
    status, data = request(base, "POST", "/api/auth/login", {"username": username, "password": new_password})
    assert status == 400, f"old password should fail after reset: {status} {data}"
    status, data = request(base, "POST", "/api/auth/login", {"username": username, "password": reset_password})
    assert status == 200, f"login with reset password failed: {status} {data}"
    print("[PASS] password reset invalidated old password")

    # 14. 恢复码一次性：二次使用应失败
    status, data = request(base, "POST", "/api/auth/reset", {
        "username": username, "recovery_code": recovery_code, "new_password": "AnotherPass000!",
    })
    assert status == 400, f"used recovery code should fail: {status} {data}"
    print("[PASS] recovery code is single-use")

    # ================= remember-me + preferences 专项（第二用户） =================
    import time as _time
    username2 = f"remember_{uuid.uuid4().hex[:8]}"
    password2 = "RememberPass123!"
    status, data = request(base, "POST", "/api/auth/register", {"username": username2, "password": password2})
    assert status == 201, f"register user2 failed: {status} {data}"
    print(f"[PASS] /api/auth/register created user {username2}")

    # 15. remember_me=false 登录：不应设置 remember cookie
    jar = CookieJar()
    status, data = request(base, "POST", "/api/auth/login", {"username": username2, "password": password2}, jar=jar)
    assert status == 200, f"login failed: {status} {data}"
    assert get_cookie_value(jar, "crayotter_remember") is None, "remember cookie should not be set"
    print("[PASS] login without remember_me sets no remember cookie")

    # 16. remember_me=true 登录：设置 remember cookie
    status, data = request(base, "POST", "/api/auth/login", {
        "username": username2, "password": password2, "remember_me": True,
    }, jar=jar)
    assert status == 200, f"remember login failed: {status} {data}"
    remember_v1 = get_cookie_value(jar, "crayotter_remember")
    assert remember_v1 and ":" in remember_v1, f"remember cookie malformed: {remember_v1}"
    print("[PASS] login with remember_me sets remember cookie")

    # 17. 模拟浏览器重开：仅携带 remember cookie，me 自动续期并轮换
    renew_headers = {"Cookie": f"crayotter_remember={remember_v1}"}
    status, data = request(base, "GET", "/api/auth/me", headers=renew_headers, jar=jar)
    assert status == 200 and data.get("renewed") is True, f"renew failed: {status} {data}"
    assert data["user"]["username"] == username2
    remember_v2 = get_cookie_value(jar, "crayotter_remember")
    assert remember_v2 and remember_v2 != remember_v1, "remember token should rotate"
    print("[PASS] /api/auth/me auto-renews from remember cookie with rotation")

    # 18. 并发宽限：立即重放旧 token → 401 但不吊销（新 token 仍可用）
    status, data = request(base, "GET", "/api/auth/me", headers={"Cookie": f"crayotter_remember={remember_v1}"})
    assert status == 401, f"stale token should 401: {status} {data}"
    status, data = request(base, "GET", "/api/auth/me", headers={"Cookie": f"crayotter_remember={remember_v2}"}, jar=jar)
    assert status == 200, f"new token should still work: {status} {data}"
    remember_v3 = get_cookie_value(jar, "crayotter_remember") or remember_v2
    print("[PASS] stale token within grace window does not trigger revocation")

    # 19. 盗窃检测：静默期后重放旧 token → 吊销全部 remember tokens
    _time.sleep(11)
    status, data = request(base, "GET", "/api/auth/me", headers={"Cookie": f"crayotter_remember={remember_v2}"})
    assert status == 401, f"replayed token should 401: {status} {data}"
    status, data = request(base, "GET", "/api/auth/me", headers={"Cookie": f"crayotter_remember={remember_v3}"})
    assert status == 401, f"all tokens should be revoked after reuse detection: {status} {data}"
    print("[PASS] reuse detection revokes all remember tokens")

    # 20. preferences：登录后 PUT/GET 合并更新
    jar = CookieJar()
    status, data = request(base, "POST", "/api/auth/login", {"username": username2, "password": password2}, jar=jar)
    assert status == 200, f"re-login failed: {status} {data}"
    status, data = request(base, "POST", "/api/auth/preferences", {"preferences": {"lastMode": "real", "taskDraft": "hello"}}, jar=jar)
    assert status == 200 and data["preferences"]["lastMode"] == "real", f"preferences put failed: {status} {data}"
    status, data = request(base, "POST", "/api/auth/preferences", {"preferences": {"currentView": "jobs"}}, jar=jar)
    assert status == 200 and data["preferences"]["lastMode"] == "real" and data["preferences"]["currentView"] == "jobs", \
        f"preferences merge failed: {status} {data}"
    status, data = request(base, "GET", "/api/auth/preferences", jar=jar)
    assert status == 200 and data["preferences"]["taskDraft"] == "hello", f"preferences get failed: {status} {data}"
    print("[PASS] /api/auth/preferences merge update and read")

    # 21. preferences：非法 key 拒绝；未登录 401
    status, data = request(base, "POST", "/api/auth/preferences", {"preferences": {"__evil__": 1}}, jar=jar)
    assert status == 400, f"invalid preference key should 400: {status} {data}"
    status, data = request(base, "GET", "/api/auth/preferences")
    assert status == 401, f"preferences without auth should 401: {status} {data}"
    print("[PASS] preferences rejects invalid key and requires auth")

    # 22. 改密后 remember token 被吊销
    jar2 = CookieJar()
    status, data = request(base, "POST", "/api/auth/login", {
        "username": username2, "password": password2, "remember_me": True,
    }, jar=jar2)
    assert status == 200, f"login failed: {status} {data}"
    remember_v4 = get_cookie_value(jar2, "crayotter_remember")
    status, data = request(base, "POST", "/api/auth/password", {
        "old_password": password2, "new_password": "RememberPass456!",
    }, jar=jar2)
    assert status == 200, f"change password failed: {status} {data}"
    status, data = request(base, "GET", "/api/auth/me", headers={"Cookie": f"crayotter_remember={remember_v4}"})
    assert status == 401, f"remember token should be revoked after password change: {status} {data}"
    print("[PASS] password change revokes remember tokens")

    # 23. logout 吊销 remember token
    jar3 = CookieJar()
    status, data = request(base, "POST", "/api/auth/login", {
        "username": username2, "password": "RememberPass456!", "remember_me": True,
    }, jar=jar3)
    assert status == 200, f"login failed: {status} {data}"
    remember_v5 = get_cookie_value(jar3, "crayotter_remember")
    status, data = request(base, "POST", "/api/auth/logout", jar=jar3)
    assert status == 200, f"logout failed: {status} {data}"
    status, data = request(base, "GET", "/api/auth/me", headers={"Cookie": f"crayotter_remember={remember_v5}"})
    assert status == 401, f"remember token should be revoked after logout: {status} {data}"
    print("[PASS] logout revokes remember token")

    print("\nAll tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
