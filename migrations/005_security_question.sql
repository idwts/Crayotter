-- 005: 可选密保问题（忘记密码时的第二种找回方式，与恢复码并行）
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS security_question TEXT,
    ADD COLUMN IF NOT EXISTS security_answer_digest TEXT;
