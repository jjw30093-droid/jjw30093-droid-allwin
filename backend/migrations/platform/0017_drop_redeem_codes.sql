-- 0017: 兑换码(CDKEY)功能整体下架(站长明确决定,2026-08-17,不是简化或
-- 降级,是完整删除)。redeem_codes 表连同 backend/commands/redeem.py、
-- POST /api/v1/redeem、POST/GET /api/v1/admin/redeem-codes 一起移除。
--
-- 每日精选的授权路径不受影响,继续保留且是唯一入口:管理员通过
-- POST /api/v1/admin/reco/access-grants 直接为"用户 + 单条 slip"授权
-- (backend/commands/reco_access.py::grant_access,数据写入
-- reco_access_grants 表,由 0015_reco_access_grants.sql 建立)。本迁移不
-- 触碰 reco_access_grants 或任何其它表。
--
-- 本迁移前已用只读查询确认真实 data/platform.db:该库尚未应用 0014+
-- (schema_migrations 最新版本号为 13),redeem_codes 仍是 0016 之前的旧
-- schema(plan_id/duration_days 形状),且行数为 0——同 0016 迁移前的确认
-- 结论一致,这里不是假设,是本次改动时重新查询过的结果。DROP TABLE 之前
-- 也确认没有其它表 REFERENCES redeem_codes(0016 的注释已确认过一次,
-- 本次在 /tmp 临时库上重新验证 DROP 后 integrity_check 正常)。

DROP TABLE redeem_codes;
