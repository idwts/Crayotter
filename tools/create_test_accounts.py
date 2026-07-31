"""创建测试账号和管理员账号，并生成账号表。

用法：在 /opt/crayotter 目录下运行：
    CRAYOTTER_DATABASE_URL=postgresql://crayotter:crayotter@localhost:5432/crayotter \
    python tools/create_test_accounts.py
"""
from __future__ import annotations

import os
import secrets
import string
import sys
from datetime import datetime

# 确保能导入 app.backend
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backend import auth
from app.backend import db


def generate_password(length: int = 16) -> str:
    """生成包含大小写字母、数字和特殊字符的随机密码。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_+-="
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*_+-=" for c in password)
        ):
            return password


def create_accounts() -> list[dict]:
    accounts: list[dict] = []

    # 10 个测试账号
    for i in range(1, 11):
        username = f"crayotter_{i}"
        password = generate_password()
        try:
            result = auth.register(
                username,
                password,
                ip_address="127.0.0.1",
                user_agent="create_test_accounts.py",
            )
            accounts.append({
                "username": username,
                "password": password,
                "role": "user",
                "user_id": str(result["user"]["id"]),
                "tenant_id": str(result["tenant"]["id"]),
                "created_at": datetime.now().isoformat(),
            })
            print(f"Created user: {username}")
        except ValueError as exc:
            print(f"Failed to create {username}: {exc}")
            raise

    # 管理员账号
    admin_username = "crayotter_admin"
    admin_password = generate_password()
    result = auth.register(
        admin_username,
        admin_password,
        ip_address="127.0.0.1",
        user_agent="create_test_accounts.py",
    )

    # 设置管理员角色
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = 'admin' WHERE username = %s",
                (admin_username,),
            )

    accounts.append({
        "username": admin_username,
        "password": admin_password,
        "role": "admin",
        "user_id": str(result["user"]["id"]),
        "tenant_id": str(result["tenant"]["id"]),
        "created_at": datetime.now().isoformat(),
    })
    print(f"Created admin: {admin_username}")

    return accounts


def write_accounts_table(accounts: list[dict], path: str) -> None:
    lines = [
        "# Crayotter 测试账号与管理账号",
        "",
        "> 警告：本文件包含明文密码，仅供内部测试使用，请勿上传到公开仓库或分享。",
        "",
        "| 用户名 | 密码 | 角色 | 用户 ID | 租户 ID | 创建时间 |",
        "|--------|------|------|---------|---------|----------|",
    ]
    for acc in accounts:
        lines.append(
            f"| {acc['username']} | `{acc['password']}` | {acc['role']} | {acc['user_id']} | {acc['tenant_id']} | {acc['created_at']} |"
        )
    lines.append("")
    lines.append("## 用途")
    lines.append("")
    lines.append("- `crayotter_1` ~ `crayotter_10`：功能/回归测试账号。")
    lines.append("- `crayotter_admin`：管理员账号，拥有 `role='admin'`。")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Accounts table written to {path}")


if __name__ == "__main__":
    accounts = create_accounts()
    table_path = os.environ.get(
        "ACCOUNTS_TABLE_PATH",
        "docs/project-control/test-accounts.md",
    )
    os.makedirs(os.path.dirname(table_path), exist_ok=True)
    write_accounts_table(accounts, table_path)
