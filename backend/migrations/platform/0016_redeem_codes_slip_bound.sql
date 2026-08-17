-- 0016: 兑换码从"绑定 plan_id + duration_days"改造为"绑定一个具体的每日精选
-- slip_id"(2026-08-16,产品权限口径修正的收尾——按场授权改造完成后,旧的
-- plan-based 兑换码路径已经变成一个"显示兑换成功但看不到任何内容"的死功能:
-- 内容访问只看 reco_access_grants 表,grant_subscription() 写的整包订阅对它
-- 没有任何效果,真实复现见 backend/commands/redeem.py 顶部注释)。
--
-- 新语义:一个兑换码只对应一场具体的每日精选(reco_slips.id),兑换成功调用
-- backend/commands/reco_access.py::grant_access() 写入 reco_access_grants,
-- 不再调用 grant_subscription()。
--
-- 本迁移前 redeem_codes 表在真实 data/platform.db 中确认为空(只读查询验证,
-- 无任何历史行,也没有其它表 REFERENCES redeem_codes),因此直接重建为新
-- schema,不需要 0014_reco_odds_contract.sql 那种"_new 表 + 拷贝 + 替换"的
-- 非破坏性迁移路径——没有数据需要保留,也没有子表依赖需要处理级联顺序。
-- 若未来某个环境这张表意外不是空的,DROP TABLE 会让该环境的 migration 在
-- 应用前即可通过人工检查发现(schema_migrations 记录的是文件 checksum,
-- 不做静默数据丢弃前的自动检测,这里用注释如实记录已验证的前提)。

DROP TABLE redeem_codes;

CREATE TABLE redeem_codes (
  id            TEXT PRIMARY KEY,              -- UUID
  code_hash     TEXT NOT NULL UNIQUE,          -- 兑换码只存 SHA-256,明文仅生成时展示一次
  slip_id       TEXT NOT NULL REFERENCES reco_slips(id),  -- 这个码兑换的具体每日精选
  batch_id      TEXT,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','used','revoked','expired')),
  created_by    TEXT REFERENCES users(id),
  created_at    TEXT NOT NULL,
  expires_at    TEXT,
  used_by       TEXT REFERENCES users(id),
  used_at       TEXT
);
CREATE INDEX idx_redeem_codes_batch ON redeem_codes(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX idx_redeem_codes_slip ON redeem_codes(slip_id);
