"""密保问题 API 测试：注册可选密保、查询问题、密保答案重置密码。"""
from __future__ import annotations

import sys
import uuid

import requests
requests.packages.urllib3.disable_warnings()  # 自签证书
_ORIG_SESSION_REQUEST = requests.Session.request
def _insecure_session_request(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    return _ORIG_SESSION_REQUEST(self, *args, **kwargs)
requests.Session.request = _insecure_session_request

BASE = "https://8.161.229.68"


def main() -> int:
    s = requests.Session()
    u = f"sq_{uuid.uuid4().hex[:8]}"
    q, a = "我的第一只宠物叫什么？", "  小白  "  # 带空格测归一化

    # 1. 注册不带密保 → question 为 null
    u0 = f"sq0_{uuid.uuid4().hex[:8]}"
    r = s.post(f"{BASE}/api/auth/register", json={"username": u0, "password": "Sq12345678",
        "agree_terms": True}, timeout=15)
    assert r.status_code == 201, r.text
    r = requests.get(f"{BASE}/api/auth/security-question", params={"username": u0}, timeout=15)
    assert r.status_code == 200 and r.json()["question"] is None, r.text
    print("[PASS] 01 register without question → question null")

    # 2. 注册带密保
    r = s.post(f"{BASE}/api/auth/register", json={
        "username": u, "password": "Sq12345678",
        "security_question": q, "security_answer": a,
        "agree_terms": True}, timeout=15)
    assert r.status_code == 201, r.text
    print("[PASS] 02 register with security question")

    # 3. 只填问题不填答案 → 400
    u_bad = f"sqb_{uuid.uuid4().hex[:8]}"
    r = requests.post(f"{BASE}/api/auth/register", json={
        "username": u_bad, "password": "Sq12345678", "security_question": q,
        "agree_terms": True}, timeout=15)
    assert r.status_code == 400, r.text
    print("[PASS] 03 question without answer → 400")

    # 4. 查询问题
    r = requests.get(f"{BASE}/api/auth/security-question", params={"username": u}, timeout=15)
    assert r.status_code == 200 and r.json()["question"] == q, r.text
    print("[PASS] 04 security-question returns question")

    # 5. 错误答案重置 → 400
    r = requests.post(f"{BASE}/api/auth/reset", json={
        "username": u, "security_answer": "错误答案", "new_password": "SqNewPass99",
    }, timeout=15)
    assert r.status_code == 400, r.text
    print("[PASS] 05 wrong answer → 400")

    # 6. 正确答案（大小写+空格归一）重置 → 200，新密码可登录
    r = requests.post(f"{BASE}/api/auth/reset", json={
        "username": u, "security_answer": "小白", "new_password": "SqNewPass99",
    }, timeout=15)
    assert r.status_code == 200, r.text
    print("[PASS] 06 correct answer reset → 200")

    r = requests.post(f"{BASE}/api/auth/login", json={"username": u, "password": "SqNewPass99",
        "agree_terms": True}, timeout=15)
    assert r.status_code == 200, r.text
    print("[PASS] 07 login with new password")

    # 8. 恢复码路径不受影响
    r = requests.post(f"{BASE}/api/auth/reset", json={
        "username": u, "recovery_code": "deadbeef", "new_password": "SqNewPass100",
    }, timeout=15)
    assert r.status_code == 400, r.text
    print("[PASS] 08 recovery-code path still enforced")

    print("\nAll security-question API tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
