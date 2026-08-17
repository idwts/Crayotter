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
import json
import logging
import secrets
import threading
import time
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

# 持久登录（remember-me）配置，对齐 OWASP Remember Me Cheat Sheet：
# - cookie 值 = "selector:validator"；selector 定位记录，validator 校验身份。
# - 数据库只存 validator 的 SHA-256 digest；每次成功使用立即轮换 validator。
# - selector 命中但 digest 不匹配视为令牌被盗（reuse detection），吊销该用户全部 remember tokens。
REMEMBER_SELECTOR_BYTES = 12
REMEMBER_VALIDATOR_BYTES = 32
REMEMBER_DAYS = 30
MAX_REMEMBER_TOKENS_PER_USER = 10
MAX_PREFERENCES_BYTES = 16 * 1024  # preferences JSONB 上限 16KB

# 认证接口防爆破（2026-08-11）：同一 IP+账号 窗口内连续失败超限即锁定。
# 进程内内存实现——单实例部署足够；多实例时需换共享存储。
AUTH_FAILURE_WINDOW_SECONDS = 600
AUTH_FAILURE_MAX = 5
AUTH_LOCK_SECONDS = 900
# 注册按 IP 限频（防批量注册）
REGISTER_WINDOW_SECONDS = 3600
REGISTER_MAX_PER_IP = 20


class AuthThrottledError(RuntimeError):
    """触发认证限流/锁定。retry_after 为建议等待秒数。"""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        minutes = max(1, self.retry_after // 60)
        super().__init__(f"尝试次数过多，请约 {minutes} 分钟后再试")


class FailureLockout:
    """线程安全的失败计数锁定器：窗口内失败达上限后锁定 lock_seconds。"""

    def __init__(self, *, max_failures: int, window_seconds: int, lock_seconds: int) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._lock_seconds = lock_seconds
        self._failures: dict[str, list[float]] = {}
        self._mutex = threading.Lock()

    def check(self, key: str) -> None:
        """若已锁定则抛 AuthThrottledError。"""
        now = time.monotonic()
        with self._mutex:
            failures = [t for t in self._failures.get(key, []) if now - t < self._window]
            if failures:
                self._failures[key] = failures
            else:
                self._failures.pop(key, None)
            if len(failures) >= self._max:
                # 锁定至最早一次失败滑出窗口后再过 lock_seconds
                raise AuthThrottledError(failures[0] + self._window + self._lock_seconds - now)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._mutex:
            failures = [t for t in self._failures.get(key, []) if now - t < self._window]
            failures.append(now)
            self._failures[key] = failures

    def record_success(self, key: str) -> None:
        with self._mutex:
            self._failures.pop(key, None)


def _throttle_key(ip_address: str | None, username: str) -> str:
    return f"{ip_address or 'unknown'}|{username.strip().lower()}"


login_lockout = FailureLockout(
    max_failures=AUTH_FAILURE_MAX,
    window_seconds=AUTH_FAILURE_WINDOW_SECONDS,
    lock_seconds=AUTH_LOCK_SECONDS,
)
register_limiter = FailureLockout(
    max_failures=REGISTER_MAX_PER_IP,
    window_seconds=REGISTER_WINDOW_SECONDS,
    lock_seconds=REGISTER_WINDOW_SECONDS,
)


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
    security_question: str | None = None,
    security_answer: str | None = None,
) -> dict[str, Any]:
    """用户注册。返回用户信息、租户信息、明文恢复码列表。密保问题可选，与答案必须成对提供。"""
    normalized = username.strip().lower()
    if len(normalized) < 2:
        raise ValueError("用户名过短")
    if len(password) < 8:
        raise ValueError("密码强度不足，至少 8 位")

    question = (security_question or "").strip()
    answer_digest: str | None = None
    if question or (security_answer or "").strip():
        if not question or not (security_answer or "").strip():
            raise ValueError("密保问题与答案必须同时填写")
        if len(question) > 200:
            raise ValueError("密保问题过长")
        answer = security_answer.strip()
        if len(answer) > 200:
            raise ValueError("密保答案过长")
        answer_digest = _sha256(answer.lower())

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
                INSERT INTO users (tenant_id, username, password_hash, password_algorithm_version, security_question, security_answer_digest)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, tenant_id, username, status, created_at
                """,
                (tenant_id, normalized, password_hash, version, question or None, answer_digest),
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
    remember_me: bool = False,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """用户登录。返回 session token 和用户信息；remember_me=True 时附带持久 remember token。"""
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

    result: dict[str, Any] = {
        "token": token,
        "expires_at": expires_at.isoformat(),
        "user": {
            "id": user_id,
            "tenant_id": tenant_id,
            "username": user["username"],
        },
    }
    if remember_me:
        result["remember_token"] = issue_remember_token(
            user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        result["remember_expires_days"] = REMEMBER_DAYS
    return result


def create_session(
    user_id: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """为用户创建新 session，返回 (token, expires_at ISO)。"""
    token = secrets.token_hex(SESSION_TOKEN_BYTES)
    token_digest = _derive_token_digest(token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions (token_digest, user_id, expires_at, ip_address, user_agent)
                VALUES (%s, %s, %s, %s::inet, %s)
                """,
                (token_digest, user_id, expires_at, ip_address, user_agent),
            )
    return token, expires_at.isoformat()


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


# ---------------------------------------------------------------------------
# 持久登录（remember-me）令牌：selector:validator 双段 + 轮换 + 盗窃检测
# ---------------------------------------------------------------------------

def issue_remember_token(
    user_id: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """签发持久 remember token，返回 "selector:validator" 明文（仅存 digest）。"""
    selector = secrets.token_hex(REMEMBER_SELECTOR_BYTES)
    validator = secrets.token_hex(REMEMBER_VALIDATOR_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REMEMBER_DAYS)

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            # 限制单用户 remember token 数量（最旧的先删）
            cur.execute(
                """
                SELECT selector FROM remember_tokens
                WHERE user_id = %s AND expires_at > now()
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            if len(rows) >= MAX_REMEMBER_TOKENS_PER_USER:
                to_delete = [r["selector"] for r in rows[MAX_REMEMBER_TOKENS_PER_USER - 1 :]]
                cur.executemany(
                    "DELETE FROM remember_tokens WHERE selector = %s",
                    [(s,) for s in to_delete],
                )

            cur.execute(
                """
                INSERT INTO remember_tokens (selector, user_id, validator_digest, expires_at, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s::inet, %s)
                """,
                (selector, user_id, _sha256(validator), expires_at, ip_address, user_agent),
            )
    return f"{selector}:{validator}"


def parse_remember_token(token: str | None) -> tuple[str, str] | None:
    """解析 "selector:validator"，非法格式返回 None。"""
    if not token or ":" not in token:
        return None
    selector, _, validator = token.partition(":")
    if len(selector) != REMEMBER_SELECTOR_BYTES * 2 or len(validator) != REMEMBER_VALIDATOR_BYTES * 2:
        return None
    return selector, validator


def verify_and_rotate_remember_token(
    token: str | None,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any] | None:
    """验证 remember token。成功时轮换 validator 并返回 {user, new_token}。

    盗窃检测：selector 命中但 validator digest 不匹配 → 吊销该用户全部
    remember tokens 并审计，随后按无效处理（返回 None）。

    并发安全（Jaspan 持久登录模式的已知 race）：浏览器重开时可能并发多个
    请求携带同一旧 validator。轮换 UPDATE 采用乐观锁（WHERE 旧 digest），
    失败按并发处理；digest 不匹配但 10 秒内刚被轮换过也按并发处理，
    不误报盗窃。只有"陈旧 token 在静默期后重放"才判定被盗。
    """
    parsed = parse_remember_token(token)
    if parsed is None:
        return None
    selector, validator = parsed
    now = datetime.now(timezone.utc)

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rt.user_id, rt.validator_digest, rt.expires_at, rt.last_used_at,
                       u.tenant_id, u.username, u.status
                FROM remember_tokens rt
                JOIN users u ON rt.user_id = u.id
                WHERE rt.selector = %s
                """,
                (selector,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            old_digest = row["validator_digest"]
            if not hmac.compare_digest(old_digest, _sha256(validator)):
                last_used = row["last_used_at"]
                if last_used is not None and (now - last_used).total_seconds() < 10:
                    # 并发重开：另一个请求刚轮换过，按并发处理（不吊销、不签发）
                    return None
                # reuse detection：令牌疑似被盗，吊销该用户全部 remember tokens
                cur.execute(
                    "DELETE FROM remember_tokens WHERE user_id = %s",
                    (row["user_id"],),
                )
                _audit_with_conn(
                    conn,
                    action="user.remember_reuse_detected",
                    actor_id=row["user_id"],
                    target_type="remember_token",
                    target_id=selector[:16],
                    ip_address=ip_address,
                )
                return None

            if row["expires_at"] <= now or row["status"] != "active":
                cur.execute("DELETE FROM remember_tokens WHERE selector = %s", (selector,))
                return None

            # 轮换 validator（缩小被盗窗口）；乐观锁防并发双轮换
            new_validator = secrets.token_hex(REMEMBER_VALIDATOR_BYTES)
            new_expires_at = now + timedelta(days=REMEMBER_DAYS)
            cur.execute(
                """
                UPDATE remember_tokens
                SET validator_digest = %s, expires_at = %s, last_used_at = now(),
                    ip_address = %s::inet, user_agent = %s
                WHERE selector = %s AND validator_digest = %s
                """,
                (_sha256(new_validator), new_expires_at, ip_address, user_agent, selector, old_digest),
            )
            if cur.rowcount != 1:
                # 并发：另一个请求已完成轮换，本次按无效处理
                return None

    return {
        "user": {
            "id": row["user_id"],
            "tenant_id": row["tenant_id"],
            "username": row["username"],
        },
        "new_token": f"{selector}:{new_validator}",
    }


def revoke_remember_token(token: str | None) -> bool:
    """吊销指定 remember token（登出时调用）。"""
    parsed = parse_remember_token(token)
    if parsed is None:
        return False
    selector, _ = parsed
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM remember_tokens WHERE selector = %s RETURNING user_id",
                (selector,),
            )
            return cur.fetchone() is not None


def revoke_all_remember_tokens(user_id: str) -> None:
    """改密、重置密码、盗窃检测后吊销该用户全部 remember tokens。"""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM remember_tokens WHERE user_id = %s",
                (user_id,),
            )


# ---------------------------------------------------------------------------
# per-user 历史动作记忆（preferences JSONB）
# ---------------------------------------------------------------------------

def get_preferences(user_id: str) -> dict[str, Any]:
    """读取用户偏好（历史动作记忆），不存在时返回空 dict。"""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT preferences FROM users WHERE id = %s AND status = 'active'",
                (user_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise KeyError("user not found")
    prefs = row["preferences"]
    return dict(prefs) if isinstance(prefs, dict) else {}


def update_preferences(user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """浅合并更新用户偏好并返回合并结果。仅接受 dict，大小受限。"""
    if not isinstance(patch, dict):
        raise ValueError("preferences must be an object")
    # 顶层 key 白名单式约束：禁止 __ 开头与过长 key，防止注入奇怪结构
    for key in patch:
        if not isinstance(key, str) or key.startswith("__") or len(key) > 64:
            raise ValueError(f"invalid preference key: {key!r}")

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET preferences = preferences || %s::jsonb
                WHERE id = %s AND status = 'active'
                RETURNING preferences
                """,
                (psycopg2.extras.Json(patch), user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError("user not found")
            merged = row["preferences"] if isinstance(row["preferences"], dict) else {}
            if len(json.dumps(merged, ensure_ascii=False)) > MAX_PREFERENCES_BYTES:
                # 超限则只保留本次 patch 内容，避免无限膨胀
                cur.execute(
                    "UPDATE users SET preferences = %s::jsonb WHERE id = %s RETURNING preferences",
                    (psycopg2.extras.Json(patch), user_id),
                )
                merged = cur.fetchone()["preferences"]
    return dict(merged) if isinstance(merged, dict) else {}


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
        revoke_all_remember_tokens(user_id)
        _audit_with_conn(
            conn,
            action="user.change_password",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            ip_address=ip_address,
        )


def get_security_question(username: str) -> str | None:
    """按用户名查询密保问题（忘记密码流程用）。用户不存在或未设置均返回 None。"""
    normalized = username.strip().lower()
    if not normalized:
        return None
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT security_question FROM users WHERE lower(username) = %s AND status = 'active'",
                (normalized,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return row["security_question"]


def reset_password_by_security_answer(
    username: str,
    security_answer: str,
    new_password: str,
    *,
    ip_address: str | None = None,
) -> bool:
    """使用密保答案重置密码（与恢复码并行的第二种找回方式）。"""
    if len(new_password) < 8:
        raise ValueError("新密码至少 8 位")

    normalized = username.strip().lower()
    answer_digest = _sha256(security_answer.strip().lower())

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM users
                WHERE lower(username) = %s
                  AND security_answer_digest = %s
                  AND status = 'active'
                """,
                (normalized, answer_digest),
            )
            row = cur.fetchone()
            if row is None:
                _audit_with_conn(
                    conn,
                    action="user.recovery_failed",
                    target_type="user",
                    target_id=normalized,
                    details={"reason": "invalid_security_answer"},
                    ip_address=ip_address,
                )
                raise ValueError("密保答案不正确")

            user_id = row["id"]
            new_hash, version = hash_password(new_password)
            cur.execute(
                "UPDATE users SET password_hash = %s, password_algorithm_version = %s WHERE id = %s",
                (new_hash, version, user_id),
            )

        revoke_all_user_sessions(user_id, ip_address=ip_address)
        revoke_all_remember_tokens(user_id)
        _audit_with_conn(
            conn,
            action="user.reset_password",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            details={"method": "security_answer"},
            ip_address=ip_address,
        )
    return True


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
        revoke_all_remember_tokens(user_id)
        _audit_with_conn(
            conn,
            action="user.reset_password",
            actor_id=user_id,
            target_type="user",
            target_id=str(user_id),
            ip_address=ip_address,
        )
    return True
