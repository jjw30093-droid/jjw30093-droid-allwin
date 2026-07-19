-- 种子数据:roles / plans / plan_entitlements / products(全部 INSERT OR IGNORE,可重复运行)
-- 价格是数据库驱动的占位配置,Admin 可改;不在前端组件写死。

INSERT OR IGNORE INTO roles (name, description) VALUES
  ('user', '普通用户'),
  ('analyst', '分析师(内容制作)'),
  ('admin', '管理员');

INSERT OR IGNORE INTO plans (id, name_zh, description, rank, is_active, created_at) VALUES
  ('free', '免费', '基础联赛数据 + 每场最高一项概率', 0, 1, '2026-07-19T00:00:00Z'),
  ('pro', 'Pro', '完整胜平负概率、比分矩阵与深度报告', 1, 1, '2026-07-19T00:00:00Z'),
  ('premium', 'Premium', 'Pro 全部能力 + 完整赔率历史与原始数据导出', 2, 1, '2026-07-19T00:00:00Z');

-- 权益物化为并集:pro ⊇ free,premium ⊇ pro(校验时直接查本 plan 行即可)
INSERT OR IGNORE INTO plan_entitlements (plan_id, entitlement) VALUES
  ('free', 'league:epl'),
  ('free', 'prediction:top_probability'),
  ('free', 'odds:summary_delayed'),

  ('pro', 'league:epl'),
  ('pro', 'prediction:top_probability'),
  ('pro', 'odds:summary_delayed'),
  ('pro', 'league:top5'),
  ('pro', 'prediction:full_wdl'),
  ('pro', 'prediction:score_matrix'),
  ('pro', 'report:deep'),
  ('pro', 'export:basic'),

  ('premium', 'league:epl'),
  ('premium', 'prediction:top_probability'),
  ('premium', 'odds:summary_delayed'),
  ('premium', 'league:top5'),
  ('premium', 'prediction:full_wdl'),
  ('premium', 'prediction:score_matrix'),
  ('premium', 'report:deep'),
  ('premium', 'export:basic'),
  ('premium', 'odds:history_full'),
  ('premium', 'odds:raw'),
  ('premium', 'export:full'),
  ('premium', 'alert:odds');

INSERT OR IGNORE INTO products (id, plan_id, name_zh, description, duration_days, price_cents, currency, is_active, sort_order, created_at) VALUES
  ('pro_monthly', 'pro', 'Pro 月度', '按月订阅,完整概率与深度报告', 30, 3900, 'CNY', 1, 10, '2026-07-19T00:00:00Z'),
  ('pro_yearly', 'pro', 'Pro 年度', '按年订阅,约 6.4 折', 365, 29900, 'CNY', 1, 20, '2026-07-19T00:00:00Z'),
  ('premium_monthly', 'premium', 'Premium 月度', '按月订阅,含完整赔率历史与导出', 30, 9900, 'CNY', 1, 30, '2026-07-19T00:00:00Z'),
  ('premium_yearly', 'premium', 'Premium 年度', '按年订阅,约 6.7 折', 365, 79900, 'CNY', 1, 40, '2026-07-19T00:00:00Z');
