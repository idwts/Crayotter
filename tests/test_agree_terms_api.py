# -*- coding: utf-8 -*-
"""用户协议同意校验线上 API 测试。

1. 注册不带 agree_terms → 400
2. 注册 agree_terms=false → 400
3. 注册 agree_terms=true → 201
4. 登录不带 agree_terms → 400
5. 登录 agree_terms=true → 200

运行：python tests/test_agree_terms_api.py
"""
import secrets
import sys

import requests
requests.packages.urllib3.disable_warnings()  # 自签证书
_ORIG_SESSION_REQUEST = requests.Session.request
def _insecure_session_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _ORIG_SESSION_REQUEST(self, *args, **kwargs)
requests.Session.request = _insecure_session_request

BASE = "https://8.161.229.68"
requests.packages.urllib3.disable_warnings()  # 自签证书

step = 0


def check(ok: bool, label: str) -> None:
    global step
    if not ok:
        print(f"[FAIL] {label}")
        sys.exit(1)
    step += 1
    print(f"[PASS] {step:02d} {label}")


def main() -> None:
    s = requests.Session()
    s.verify = False
    name = f"agree_{secrets.token_hex(3)}"
    pwd = "agreePass123!"

    r = s.post(f"{BASE}/api/auth/register", json={"username": name, "password": pwd})
    check(r.status_code == 400, f"注册缺 agree_terms → 400（实际 {r.status_code}）")

    r = s.post(f"{BASE}/api/auth/register", json={"username": name, "password": pwd, "agree_terms": False})
    check(r.status_code == 400, f"注册 agree_terms=false → 400（实际 {r.status_code}）")

    r = s.post(f"{BASE}/api/auth/register", json={"username": name, "password": pwd, "agree_terms": True})
    check(r.status_code == 201, f"注册 agree_terms=true → 201（实际 {r.status_code}）")

    r = s.post(f"{BASE}/api/auth/login", json={"username": name, "password": pwd})
    check(r.status_code == 400, f"登录缺 agree_terms → 400（实际 {r.status_code}）")

    r = s.post(f"{BASE}/api/auth/login", json={"username": name, "password": pwd, "agree_terms": True})
    check(r.status_code == 200, f"登录 agree_terms=true → 200（实际 {r.status_code}）")

    print(f"\n全部 {step} 步通过")


if __name__ == "__main__":
    main()
