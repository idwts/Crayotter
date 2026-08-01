"""账号认证服务：注册、登录、Session、恢复码、密码管理、审计日志。

智能体速记:
- 密码使用 SHA-256 + 独立随机盐（第一版规划）。
- Session token 为 32 字节随机 hex，数据库只存 SHA-256 digest。
- 所有写操作默认写 audit_logs。
- 依赖 app.backend.db 连接 PostgreSQL。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras

from app.backend import db

logger = logging.getLogger(__name__)

PASSWORD_ALGORITHM_VERSION = 1
SESSION_TOKEN_BYTES = 32
SESSION_DAYS = 30
RECOVERY_CODE_COUNT = 8
MAX_SESSIONS_PER_USER = 10


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def hash_password(password: str) -> tuple[str, int]:
    """返回 (password_hash, algorithm_version)。

    格式: version:hex_salt:hex_hash
    """
    salt = secrets.token_hex(16)
    hashed = _sha256(salt.encode() + password.encode())
    return f"{PASSWORD_ALGORITHM_VERSION}:{salt}:{hashed}", PASSWORD_ALGORITHM_VERSION


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码，并支持渐进迁移算法版本。"""
    try:
        version_str, salt, stored_hash = password_hash.split(":", 2)
        version = int(version_str)
    except Exception:
        return False
    if version == 1:
        computed = _sha256(salt.encode() + password.encode())
        return hmac.compare_digest(computed, stored_hash)
    return False


def _derive_token_digest(token: str) -> str:
    return _sha256(token)


def _audit_with_conn(
    conn: Any,
    *,
    action: str,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (actor_id, action, target_type, target_id, details, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s::inet)
            """,
            (actor_id, action, target_type, target_id, psycopg2.extras.Json(details) if details is not None else None, ip_address),
        )


def register(
    username: str,
    password: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """用户注册。返回用户信息、租户信息、明文恢复码列表。"""
    normalized = username.strip().lower()
    if len(normalized) < 2:
        raise ValueError("用户名过短")
    if len(password) < 8:
        raise ValueError("密码强度不足，至少 8 位")

    password_hash, version = hash_password(password)

    with db.get_connection() as conn:
        # 预先检查用户名是否已存在，给出明确错误
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE lower(username) = %s", (normalized,))
            if cur.fetchone() is not None:
                raise ValueError("用户名已存在")

        # 创建租户
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id, name, created_at",
                (f"tenant-{normalized}",),
            )
            tenant = cur.fetchone()
            tenant_id = tenant["id"]

            # 创建用户
            cur.execute(
                """
                INSERT INTO users (tenant_id, username, password_hash, password_algorithm_version)
                VALUES (%s, %s, %s, %s)
                RETURNING id, tenant_id, username, status, created_at
                """,
                (tenant_id, normalized, password_hash, version),
            )
            user = cur.fetchone()
            user_id = user["id"]

            # 生成恢复码
            codes_plain = [secrets.token_hex(4) for _ in range(RECOVERY_CODE_COUNT)]
            for code in codes_plain:
                cur.execute(
                    "INSERT INTO recovery_codes (user_id, code_digest) VALUES (%s, %s)",
                    (user_id, _sha256(code)),
                )

            _audit_with_conn(
                conn,
                action="user.register",
                actor_id=user_id,
                target_type="user",
                target_id=str(user_id),
                ip_address=ip_address,
            )

    # 将 RealDictRow 中的 datetime 转为 ISO 字符串，便于 JSON 序列化
    user_dict = dict(user)
    tenant_dict = dict(tenant)
    for d in (user_dict, tenant_dict):
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
    return {
        "user": user_dict,
        "tenant": tenant_dict,
        "recovery_codes": codes_plain,
    }


def login(
    username: str,
    password: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """用户登录。返回 session token 和用户信息。"""
    normalized = username.strip().lower()

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, username, password_hash, status
                FROM users
                WHERE lower(username) = %s
                """,
                (normalized,),
            )
            user = cur.fetchone()

        if user is None or not verify_password(password, user["password_hash"]):
            _audit_with_conn(
                conn,
                action="user.login_failed",
                target_type="user",
                target_id=normalized,
                details={"reason": "invalid_credentials"},
                ip_address=ip_address,
            )
            raise ValueError("用户名或密码错误")

        if user["status"] != "active":
            raise ValueError("账号已被禁用")

        user_id = user["id"]
        tenant_id = user["tenant_id"]

        # 限制单用户最大 session 数（最旧的先吊销）
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT token_digest FROM sessions
                WHERE user_id = %s AND revoked_at IS NULL AND expires_at > now()
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            sessions = cur.fetchall()
            if len(sessions) >= MAX_SESSIONS_PER_USER:
                to_revoke = [s["token_digest"] for s in sessions[MAX_SESSIONS_PER_USER - 1 :]]
                cur.executemany(
                    "UPDATE sessions SET revoked_at = now() WHERE token_digest = %s",
                    [(t,) for t in to_revoke],
                )

        token = secrets.token_hex(SESSION_TOKEN_BYTES)
        token_digest = _derive_token_digest(token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (token_digest, user_id, expires_at, ip_address, user_agent)
                VALUES (%s, %s, %s, %s::inet, %s)
                """,
                (token_digest, user_id, expires_at, ip_address, user_agent),
            )

            _audit_with_conn(
                conn,
                action="user.login",
                actor_id=user_id,
                target_type="session",
                target_id=token_digest[:16],
                ip_address=ip_address,
            )

    return {
        "token": token,
        "expires_at": expires_at.isoformat(),
        "user": {
            "id": user_id,
            "tenant_id": tenant_id,
            "username": user["username"],
        },
    }


def logout(token: str, *, ip_address: str | None = None) -> bool:
    """注销指定 session。"""
    token_digest = _derive_token_digest(token)
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sessions SET revoked_at = now()
                WHERE token_digest = %s AND revoked_at IS NULL
                RETURNING user_id
                """,
                (token_digest,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            _audit_with_conn(
                conn,
                action="user.logout",
                actor_id=row["user_id"],
                target_type="session",
                target_id=token_digest[:16],
                ip_address=ip_address,
            )
    return True


def revoke_all_user_sessions(user_id: str, *, ip_address: str | None = None) -> None:
    """修改密码、管理员重置、禁用账号后吊销全部 session。"""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )
        _audit_with_conn(
            conn,
            action="user.revoke_all_sessions",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            ip_address=ip_address,
        )


def get_user_by_token(token: str | None) -> dict[str, Any] | None:
    """通过 session token 获取当前用户与租户信息。"""
    if not token:
        return None
    token_digest = _derive_token_digest(token)
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.tenant_id, u.username, u.status
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token_digest = %s
                  AND s.revoked_at IS NULL
                  AND s.expires_at > now()
                  AND u.status = 'active'
                """,
                (token_digest,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def change_password(
    user_id: str,
    old_password: str,
    new_password: str,
    *,
    ip_address: str | None = None,
) -> None:
    """修改密码，成功后吊销全部旧 session。"""
    if len(new_password) < 8:
        raise ValueError("新密码至少 8 位")

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash FROM users WHERE id = %s AND status = 'active'",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None or not verify_password(old_password, row["password_hash"]):
                raise ValueError("原密码错误")

            new_hash, version = hash_password(new_password)
            cur.execute(
                "UPDATE users SET password_hash = %s, password_algorithm_version = %s WHERE id = %s",
                (new_hash, version, user_id),
            )

        revoke_all_user_sessions(user_id, ip_address=ip_address)
        _audit_with_conn(
            conn,
            action="user.change_password",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            ip_address=ip_address,
        )


def reset_password_by_recovery_code(
    username: str,
    recovery_code: str,
    new_password: str,
    *,
    ip_address: str | None = None,
) -> bool:
    """使用一次性恢复码重置密码。"""
    if len(new_password) < 8:
        raise ValueError("新密码至少 8 位")

    normalized = username.strip().lower()
    code_digest = _sha256(recovery_code)

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rc.id AS code_id, rc.user_id
                FROM recovery_codes rc
                JOIN users u ON rc.user_id = u.id
                WHERE lower(u.username) = %s
                  AND rc.code_digest = %s
                  AND rc.used_at IS NULL
                  AND u.status = 'active'
                """,
                (normalized, code_digest),
            )
            row = cur.fetchone()
            if row is None:
                _audit_with_conn(
                    conn,
                    action="user.recovery_failed",
                    target_type="user",
                    target_id=normalized,
                    details={"reason": "invalid_recovery_code"},
                    ip_address=ip_address,
                )
                raise ValueError("恢复码无效或已使用")

            user_id = row["user_id"]
            new_hash, version = hash_password(new_password)
            cur.execute(
                "UPDATE users SET password_hash = %s, password_algorithm_version = %s WHERE id = %s",
                (new_hash, version, user_id),
            )
            cur.execute(
                "UPDATE recovery_codes SET used_at = now() WHERE id = %s",
                (row["code_id"],),
            )

        revoke_all_user_sessions(user_id, ip_address=ip_address)
        _audit_with_conn(
            conn,
            action="user.reset_password",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            ip_address=ip_address,
        )
    return True
