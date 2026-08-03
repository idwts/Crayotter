-- 迁移 004：用户级模型配置（BYOK 持久化）。
-- 智能体速记: 密钥字段一律存 Fernet 密文（app/backend/model_config.py 加解密），
-- 本表绝不存明文 key；use_own_key=false 时任务走运营者平台配额。
BEGIN;

CREATE TABLE IF NOT EXISTS user_model_configs (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    use_own_key BOOLEAN NOT NULL DEFAULT FALSE,
    api_key_enc TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    video_api_key_enc TEXT NOT NULL DEFAULT '',
    video_base_url TEXT NOT NULL DEFAULT '',
    video_model_name TEXT NOT NULL DEFAULT '',
    tts_api_key_enc TEXT NOT NULL DEFAULT '',
    tts_base_url TEXT NOT NULL DEFAULT '',
    tts_model_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
