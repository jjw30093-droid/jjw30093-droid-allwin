-- 0015: 每日精选按"用户 + 单条推荐单(slip)"授权(2026-08-16,产品权限口径
-- 修正,经用户批准)——取代旧的全局 reco:daily 布尔权益(持有 daily_picks
-- 订阅即可看近 30 天全部推荐单)。
--
-- 新正式规则(CLAUDE.md §8 三段可见性修订):除"每日精选"外全站比赛内容
-- 免费(含匿名);"每日精选"是全站唯一需要 admin 授权的内容,授权必须按
-- "用户 + 单条 slip"授予,用户获得一场的权限不得因此看到其它场次。
--
-- 按 slip_id(不是 match_id)授权:reco_slips 可能是单场也可能是串关
-- (parlay,combo_type='parlay',跨多场比赛),内容作为整体单元发布展示,
-- slip 是这个代码库里最自然的内容寻址单位——用户对一个 slip 的授权自动
-- 覆盖它包含的全部腿(不管单场还是跨场串关)。"拿到 A 场权限不能看到 B 场"
-- 仍然成立:A 场和 B 场如果是两个不同的 reco_slip(两张单场单),天然是
-- 两条独立的授权记录。
--
-- 本迁移只新增表,不改写/不迁移任何既有数据:现有 active 的 daily_picks
-- 订阅**不会**被自动转换成 reco_access_grants 记录——自动批量转换等价于
-- 变相保留了旧全局解锁效果,是本次修正明确要禁止的行为。这些历史订阅行
-- 本身不删除(不做破坏性 migration),只是此后不再被任何内容访问判定读取。

CREATE TABLE reco_access_grants (
  id            TEXT PRIMARY KEY,             -- UUID
  user_id       TEXT NOT NULL REFERENCES users(id),
  slip_id       TEXT NOT NULL REFERENCES reco_slips(id),
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  granted_at    TEXT NOT NULL,
  granted_by    TEXT NOT NULL REFERENCES users(id),
  revoked_at    TEXT,
  revoked_by    TEXT REFERENCES users(id),
  note          TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX idx_reco_access_grants_user ON reco_access_grants(user_id);
CREATE INDEX idx_reco_access_grants_slip ON reco_access_grants(slip_id);

-- 同一用户对同一 slip 不能同时存在两条 active 授权(数据库层兜底;应用层
-- backend/commands/reco_access.py::grant_access 已经先查后插做同样的幂等,
-- 这里是防御纵深,防止绕过应用层直接写库产生重复 active 行)。
CREATE UNIQUE INDEX uq_reco_access_grants_active_user_slip
  ON reco_access_grants(user_id, slip_id) WHERE status = 'active';
