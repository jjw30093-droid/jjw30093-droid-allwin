-- 免费/登录联赛矩阵互换(用户拍板,见 docs/current-state.md 与
-- backend/queries/leagues.py 的 LEAGUE_META 同批调整)。
--
-- 背景:MVP 早期把"英超 + 9 个中国竞彩常见小联赛(league:lottery)"设为免费,
-- 五大联赛(top5)设为登录门槛。实测显示这个矩阵与内容现实相反——五大联赛
-- 是内容最厚、更新最频繁的头部联赛,而 9 个小联赛数据薄弱。本迁移把免费面
-- 换成"五大联赛 + 欧战三项(欧冠/欧联/欧协联)"，9 个小联赛转为登录可见。
--
-- 新增 entitlement:league:european_cup(欧冠 42/欧联 73/欧协联 10216，见
-- LEAGUE_META 同批代码改动，从 league:lottery 改挂到这个新 entitlement）。
-- 之前持有 league:lottery 的计划（free/pro/premium/member/daily_picks）
-- 必须同时拿到 league:european_cup，否则欧战三项会从付费/会员计划里消失——
-- 本迁移只扩大 free 的可见面，不得缩小任何既有计划的可见面。
--
-- 幂等：INSERT OR IGNORE 可重复执行；DELETE 只删 free 一行 league:lottery，
-- 重复执行时该行已不存在，DELETE 是空操作，同样幂等。
INSERT OR IGNORE INTO plan_entitlements (plan_id, entitlement) VALUES
  ('free', 'league:top5'),
  ('free', 'league:european_cup'),
  ('pro', 'league:european_cup'),
  ('premium', 'league:european_cup'),
  ('member', 'league:european_cup'),
  ('daily_picks', 'league:european_cup');

-- free 不再免费访问 9 个小联赛（英冠/荷甲/葡超/巴甲/挪超/瑞超/日职/韩K/澳超）；
-- 这些联赛现在需要登录（member 及以上计划仍持有 league:lottery，不受影响）。
DELETE FROM plan_entitlements WHERE plan_id = 'free' AND entitlement = 'league:lottery';
