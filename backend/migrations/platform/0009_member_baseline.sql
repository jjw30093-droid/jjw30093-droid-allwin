-- 0009: 商业模式重构第一部分 —— 足球数据去付费化(CLAUDE.md §8 修订,经用户批准)。
--
-- 新三段可见性:
--   匿名        = free plan 权益(不变:英超/竞彩联赛 + 最高一项概率 + 延迟赔率摘要)
--   已登录      = 'member' 基线(旧 premium 全部足球数据权益;登录即得,无需订阅)
--   付费        = 后续独家推荐板块专用 plan(本迁移不建,见第二部分设计)
--
-- 登录的产品意义从"解锁付费内容"变为"沉淀用户 + 公众号涨粉"。
--
-- 'member' 不是可购买商品(products 无对应行),由 entitlements.resolve_entitlements
-- 对任何已登录用户自动并入(代码侧变更,见 backend/auth/entitlements.py)。
-- plan_entitlements 仍是物化并集、无跨行继承(0004 的约定不变)。
--
-- pro/premium:计划与商品在新模式下失去意义 → is_active=0 下架。
-- 行本身保留(subscriptions 历史行外键引用它们,不做破坏性删除);已有的
-- 存量订阅不受影响——其权益集是 member 基线的子集,登录基线已覆盖,不损失任何内容。

INSERT OR IGNORE INTO plans (id, name_zh, description, rank, is_active, created_at) VALUES
  ('member', '注册会员', '登录即享全部足球数据:五大联赛、完整概率、比分矩阵、深度报告、完整赔率时间线与导出', 1, 1, '2026-08-10T00:00:00Z');

INSERT OR IGNORE INTO plan_entitlements (plan_id, entitlement) VALUES
  ('member', 'league:epl'),
  ('member', 'league:lottery'),
  ('member', 'league:top5'),
  ('member', 'prediction:top_probability'),
  ('member', 'prediction:full_wdl'),
  ('member', 'prediction:score_matrix'),
  ('member', 'report:deep'),
  ('member', 'odds:summary_delayed'),
  ('member', 'odds:history_full'),
  ('member', 'odds:raw'),
  ('member', 'export:basic'),
  ('member', 'export:full'),
  ('member', 'alert:odds');

UPDATE plans SET is_active = 0 WHERE id IN ('pro', 'premium');
UPDATE products SET is_active = 0
 WHERE id IN ('pro_monthly', 'pro_yearly', 'premium_monthly', 'premium_yearly');

-- free 描述与新定位对齐(内容边界不变,只改用户可见描述)
UPDATE plans
   SET description = '无需登录:英超与竞彩常见联赛数据 + 每场最高一项概率 + 延迟赔率摘要'
 WHERE id = 'free';
