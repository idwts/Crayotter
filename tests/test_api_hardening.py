"""API 健壮性回归（2026-08-11 整体检查修复项）：

1. SSE `/jobs/{id}/events/stream` 对不存在的任务返回干净的 404 JSON
   （修复前：200 头发出后 KeyError 冒泡，404 被序列化进 body，响应损坏）。
2. GET 路由参数解析错误（如 after=abc）返回 400 而非 500
   （异常映射跨 HTTP 方法统一：ValueError/TypeError→400）。
3. GET 未知路由仍 404；未认证仍 401。

用法：python tests/test_api_hardening.py [--base-url http://8.161.229.68]
"""
from __future__ import annotations

import argparse
import sys
import uuid

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://8.161.229.68")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    session = requests.Session()
    username = f"hard_{uuid.uuid4().hex[:8]}"
    resp = session.post(f"{base}/api/auth/register", json={"username": username, "password": "Hard123456"}, timeout=15)
    assert resp.status_code == 201, f"register failed: {resp.status_code}"
    print("[PASS] 00 registered")

    failures = []

    # 1. SSE 不存在任务 → 干净 404
    resp = session.get(f"{base}/jobs/nonexistent-job/events/stream?after=0", timeout=15)
    body_ok = resp.headers.get("Content-Type", "").startswith("application/json") and "error" in resp.text
    if resp.status_code == 404 and body_ok:
        print("[PASS] 01 SSE missing job -> clean 404 JSON")
    else:
        failures.append(f"SSE missing job: status={resp.status_code} body={resp.text[:120]!r}")

    # 2. 参数解析错误 → 400（修复前 500）
    resp = session.get(f"{base}/jobs/nonexistent-job/events?after=abc", timeout=15)
    if resp.status_code == 400:
        print("[PASS] 02 invalid after param -> 400")
    else:
        failures.append(f"invalid after: status={resp.status_code}")

    # 3. 既有行为不回退
    resp = session.get(f"{base}/jobs/nonexistent-job", timeout=15)
    if resp.status_code == 404:
        print("[PASS] 03 GET missing job -> 404")
    else:
        failures.append(f"GET missing job: status={resp.status_code}")

    resp = requests.get(f"{base}/api/auth/me", timeout=15)
    if resp.status_code == 401:
        print("[PASS] 04 unauthenticated me -> 401")
    else:
        failures.append(f"unauth me: status={resp.status_code}")

    resp = session.get(f"{base}/definitely-not-a-route", timeout=15)
    if resp.status_code == 404:
        print("[PASS] 05 unknown route -> 404")
    else:
        failures.append(f"unknown route: status={resp.status_code}")

    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print("\nAll API hardening tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
