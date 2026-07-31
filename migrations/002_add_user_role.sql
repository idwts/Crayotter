-- 迁移 002：添加用户角色字段
-- 说明：为区分普通用户与管理员，添加 role 字段。

ALTER TABLE users
ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'
CHECK (role IN ('user', 'admin'));

-- 为已有管理员预留：后续可通过 UPDATE users SET role='admin' WHERE username='...' 设置。
