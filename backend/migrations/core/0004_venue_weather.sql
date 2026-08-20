-- 比赛详情页"比赛信息"卡片(2026-08-20,站长要求参照 miaomiaodi.cc 的比赛
-- 详情页加球场/温度等信息):补充场馆与天气描述,与既有 Referee/Temperature/
-- Wind_Speed(backend/schema.py 基线列,产线已有真实数据:71%/16%/16% 覆盖)
-- 同属"赛前定向抓取"的一批字段,采集机制完全复用
-- backend/cli/poll_fotmob_snapshots.py 的 --write-match-details 窄 UPDATE
-- 通道(见该文件 _write_match_details),这里只加列,不改采集逻辑本身。
--
-- 真实来源实测(tests/fixtures/fotmob/prematch-5104961.json,FotMob
-- match_details 真实响应):
--   content.matchFacts.infoBox.Stadium = {name, city, country, capacity, ...}
--   content.weather = {temperature, windSpeed, description/defaultTitle, ...}
-- Stadium 此前完全未采集(无对应列);weather.description 同理——旧代码只取了
-- 数字(temperature/windSpeed),文字描述("Partly Cloudy/Wind" 这类)被丢弃,
-- 现在把它留下来,前端才能给一个"晴/多云/雨"这样的中文天气标签,不是只有
-- 一个孤零零的摄氏度数字。
--
-- 全部可空,不回填历史值——旧比赛没有对应的 FotMob 原始抓取快照可供重新
-- 解析,留 NULL 是唯一诚实的选择(CLAUDE.md §6.2 缺失即 NULL,不猜测)。

ALTER TABLE dim_match ADD COLUMN Venue_Name TEXT;
ALTER TABLE dim_match ADD COLUMN Venue_City TEXT;
ALTER TABLE dim_match ADD COLUMN Venue_Country TEXT;
ALTER TABLE dim_match ADD COLUMN Weather_Description TEXT;
