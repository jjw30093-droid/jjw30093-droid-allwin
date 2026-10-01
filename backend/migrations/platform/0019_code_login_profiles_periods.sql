-- 0019(2026-10,登录方式改版,经站长批准):
--
-- ① 公众号发码登录:登录路线从「带参数二维码」改为「用户把网页上的 4 位验证码发给公众号」。
--    带参数二维码接口只支持「已认证服务号」(微信官方文档原文),站长的公众号是未认证的
--    个人公众号;发码登录只需要「消息推送」能力,用户 openid 取自消息的 FromUserName。
--    device_login_requests 加 login_code:4 位数字,安全随机生成;等待中(pending)的
--    请求之间互不重复(下面的部分唯一索引兜底,应用层先把过期的 pending 标 expired)。
--    旧的 qr_ticket / qr_url 列保留不删(历史行),新流程不再写入。
--
-- ② 用户编号与昵称:未认证公众号拿不到微信昵称/头像(微信 2021-12-27 起不再输出),
--    后台靠「用户编号」认人。short_code = 6 位大写十六进制;存量用户取内部 UUID 去横线
--    后的前 6 位(UUID 随机,7 个存量账号互撞概率可忽略,撞了唯一索引会让迁移直接失败、
--    不会静默写坏)。nickname_set = 用户是否已在首次登录时设过(或跳过)昵称;
--    存量密码账号(管理员)视为已设置,不弹首次登录的昵称框。
--
-- ③ 每日精选「时段授权」:后台按 用户 + 起止日期(北京时间自然日,含首尾)开通。
--    覆盖口径:发布时间(published_at,换算北京时间日期)落在 [starts_on, ends_on] 内的精选;
--    自然到期后这些精选仍可看(判定看"精选何时发布",不看"现在是何时");
--    提前撤销 = 全部收回(与撤销单场授权一致)。与 reco_access_grants(单场)并存,
--    任一有效即可看。
--
-- 本迁移只加列、加表、加索引,回填两列;不删除、不改写任何既有业务数据。

ALTER TABLE device_login_requests ADD COLUMN login_code TEXT;

CREATE UNIQUE INDEX uq_device_login_code_pending
  ON device_login_requests(login_code)
  WHERE status = 'pending' AND login_code IS NOT NULL;

ALTER TABLE users ADD COLUMN short_code TEXT;
ALTER TABLE users ADD COLUMN nickname_set INTEGER NOT NULL DEFAULT 0 CHECK (nickname_set IN (0, 1));

UPDATE users SET short_code = upper(substr(replace(id, '-', ''), 1, 6)) WHERE short_code IS NULL;
UPDATE users SET nickname_set = 1 WHERE password_hash IS NOT NULL;

CREATE UNIQUE INDEX uq_users_short_code ON users(short_code) WHERE short_code IS NOT NULL;

CREATE TABLE reco_access_periods (
  id          TEXT PRIMARY KEY,                -- UUID
  user_id     TEXT NOT NULL REFERENCES users(id),
  starts_on   TEXT NOT NULL,                   -- 北京时间 YYYY-MM-DD(含)
  ends_on     TEXT NOT NULL,                   -- 北京时间 YYYY-MM-DD(含)
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  granted_at  TEXT NOT NULL,
  granted_by  TEXT NOT NULL REFERENCES users(id),
  revoked_at  TEXT,
  revoked_by  TEXT REFERENCES users(id),
  note        TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  CHECK (ends_on >= starts_on)
);
CREATE INDEX idx_reco_access_periods_user ON reco_access_periods(user_id);
