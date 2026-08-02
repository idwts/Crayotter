-- 迁移 003：持久登录（remember-me）令牌 + 用户偏好
-- 版本: 003
-- 说明: 对齐 OWASP Remember Me / Session Management Cheat Sheet：
--       1) remember_tokens 表实现 selector:validator 双段持久令牌，
--          数据库只存 validator 的 SHA-256 digest，使用即轮换，支持盗窃检测；
--       2) users.preferences JSONB 存储 per-user 历史动作记忆（视图/草稿等）。
-- 智能体速记: 绝不在 cookie 存密码；remember cookie 值为 "selector:validator"，
--             validator_digest 不匹配时按盗窃处理并吊销该用户全部 remember tokens。

CREATE TABLE IF NOT EXISTS remember_tokens (
    selector TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    validator_digest TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_remember_tokens_user_id ON remember_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_remember_tokens_expires_at ON remember_tokens(expires_at);

-- per-user 历史动作记忆（JSONB 浅合并更新）
ALTER TABLE users
ADD COLUMN IF NOT EXISTS preferences JSONB NOT NULL DEFAULT '{}'::jsonb;
