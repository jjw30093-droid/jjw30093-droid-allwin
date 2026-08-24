-- 比赛详情页图表配色(2026-08-24,站长反映主客队固定青绿/蓝两色肉眼偏近,
-- 要求接入 FotMob 真实球队配色;经反编译 APK + 生产 API 实测确认后落地)。
-- 复用 FotMob 已做撞色规避的逐场配色,不自行重新实现其 CIE94 撞色规避算法。
--
-- 真实来源实测(tests/fixtures/fotmob/prematch-5104961.json,FotMob
-- match_details 真实响应,general.teamColors):
--   lightMode: {home, away}, darkMode: {home, away}
--   fontLightMode/fontDarkMode(文字色)本次不采集,当前用不到。
-- 这是"主客配对级"配色,已由 FotMob 服务端按对手做过撞色规避(例如主队本身
-- 是蓝色系,遇到另一支蓝色系球队时会被换成金色系替补色),不是球队固定
-- 色——同一支球队在不同比赛里这四列可能不同,这是预期行为,不是 bug。与
-- content.shotmap.shots[].teamColor(每次射门自带的球队原始基础色,没有撞
-- 色规避也没有深浅模式区分)是两个不同字段,本迁移只覆盖前者。
--
-- 全部可空,不回填历史值——旧比赛没有对应的 FotMob 原始抓取快照可供重新
-- 解析,留 NULL 是唯一诚实的选择(CLAUDE.md §6.2 缺失即 NULL,不猜测);
-- 已完赛比赛可用 backend/cli/reingest_matches.py 按 match_id 补采。

ALTER TABLE dim_match ADD COLUMN Home_Team_Color_Light TEXT;
ALTER TABLE dim_match ADD COLUMN Home_Team_Color_Dark TEXT;
ALTER TABLE dim_match ADD COLUMN Away_Team_Color_Light TEXT;
ALTER TABLE dim_match ADD COLUMN Away_Team_Color_Dark TEXT;
