-- league:lottery entitlement(中国竞彩常见非五大联赛,如瑞典超/挪超;CLAUDE.md §8.1
-- 的 entitlement 表在本次瑞典超接入前未预留这一档,这里做最小扩展)。
--
-- 与 league:top5(pro 专属)不同:league:lottery 从 free 起即可访问——基础联赛页、
-- 赛程、积分榜与免费概率投影(CLAUDE.md §8.2:免费只出胜平负中的最高一项)对匿名/
-- 免费用户同样公开,只是"多一个可浏览的联赛类别",不新增付费门槛。pro/premium
-- 沿用既有"权益物化为并集"约定(0002_seed.sql 的注释),因此三档都要显式加这一行,
-- 不能只加在 free 上指望 pro/premium"继承"——resolve_entitlements 是按 plan_id
-- 精确查本 plan 行,没有跨行继承逻辑。
--
-- 幂等:INSERT OR IGNORE,可在全新库与既有升级库重复执行;PRIMARY KEY
-- (plan_id, entitlement) 保证不会产生重复权益行。
INSERT OR IGNORE INTO plan_entitlements (plan_id, entitlement) VALUES
  ('free', 'league:lottery'),
  ('pro', 'league:lottery'),
  ('premium', 'league:lottery');
