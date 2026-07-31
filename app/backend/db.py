"""PostgreSQL 数据库连接与租户上下文工具。

智能体速记:
- 通过环境变量 CRAYOTTER_DATABASE_URL 连接 PostgreSQL。
- 所有数据库连接复用 ThreadedConnectionPool。
- 查询资源表前必须调用 set_tenant_id(conn, tenant_id) 激活 RLS。
- 若未配置数据库 URL，模块仍然可导入，但连接会抛出 RuntimeError。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.environ.get(
    "CRAYOTTER_DATABASE_URL",
    "postgresql://crayotter:crayotter@localhost:5432/crayotter",
)

_pool: ThreadedConnectionPool | None = None
_MIN_CONN = int(os.environ.get("CRAYOTTER_DB_MIN_CONN", "1") or "1")
_MAX_CONN = int(os.environ.get("CRAYOTTER_DB_MAX_CONN", "10") or "10")


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("CRAYOTTER_DATABASE_URL is not set.")
        try:
            _pool = ThreadedConnectionPool(
                _MIN_CONN,
                _MAX_CONN,
                DATABASE_URL,
                cursor_factory=RealDictCursor,
            )
            logger.info("PostgreSQL connection pool initialized.")
        except Exception as exc:
            logger.error("Failed to initialize PostgreSQL pool: %s", exc)
            raise RuntimeError(f"Database connection failed: {exc}") from exc
    return _pool


@contextmanager
def get_connection() -> Iterator[Any]:
    """获取一个数据库连接，退出时自动提交/回滚并归还连接池。"""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor() -> Iterator[Any]:
    """获取一个已设置 RealDictCursor 的游标。"""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def set_tenant_id(conn: Any, tenant_id: str | None) -> None:
    """在当前连接上设置 app.tenant_id，激活 RLS。"""
    value = str(tenant_id) if tenant_id else ""
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (value,))


def execute(
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
    *,
    tenant_id: str | None = None,
    fetch_one: bool = False,
    fetch_all: bool = False,
) -> Any:
    """简易执行封装，用于非关键路径或初始化脚本。生产代码建议显式使用 get_cursor。"""
    with get_connection() as conn:
        if tenant_id is not None:
            set_tenant_id(conn, tenant_id)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch_one:
                return cur.fetchone()
            if fetch_all:
                return cur.fetchall()
            return None


def init_pool() -> None:
    """预先初始化连接池；服务启动时调用。"""
    _get_pool()
