-- 比赛详情页对齐 FotMob(2026-08-24,站长要求):场地天气卡补容纳人数/草皮/
-- 经纬度,新增裁判信息卡(黄牌/犯规每场均值 + 联赛均值 + 服务端评级),以及
-- fact_shotmap 的乌龙球标志位。
--
-- 真实来源实测(2026-08-24 对 FotMob 生产站 15 场跨联赛抽样,键集一致):
--   content.matchFacts.infoBox.Stadium = {name, city, country, lat, long,
--     capacity, surface}  ← 恰好 7 键,long 不是 lng
--   content.weather = {..., localizedKey, iconCode, lastUpdated, ...}
--     localizedKey 是 FotMob 自己下发的枚举 key(如
--     weather_condition_partly_cloudy),可直接查 APK 中文表,比现在前端的
--     关键词正则猜译可靠
--   content.matchFacts.infoBox.Referee = {text, id, country, countryCode,
--     imgUrl(是国旗不是头像), leagueId, leagueName, stats[]}
--     stats[] 定长 6 项(matches/yellowCards/redCards/unknown/penalties/fouls),
--     perMatch 两项(yellowCards/fouls)额外带 average/total/averageType/
--     fillPercentage/averagePercentage —— averageType(below/average/above)
--     是服务端算好的评级,已用 60 条实网样本证伪"可由 fillPercentage 反推",
--     客户端一律直用不自算。
--
-- Referee_Stats_Json 单列存原样 JSON 而不拆 8 个标量列:6 项里只有 2 项带
-- 均值/评级,拆列会造成一堆恒空列;由 query 层投影更干净。
--
-- Is_Own_Goal(fact_shotmap):FotMob 把乌龙球记在"打进自家球门那一队"名下
-- (全库 1022 条 xG 为 NULL 的射门 100% 是 Outcome='Goal' 且
-- avg(X_Coord)=5.34,本方球门端),直接按 is_home 分组数进球会归错队——
-- 对照实验 400 场含乌龙球比赛错 392 场。APK ShotMapShot 第 21 字段就是
-- isOwnGoal,与已取的 11 个键同属一份 schema。历史行留 NULL,query 层用
-- "xG IS NULL AND Outcome='Goal'"推断并显式标注 inferred,不冒充采集值。
--
-- 全部可空,不回填历史值(CLAUDE.md §6.2 缺失即 NULL,不猜测);历史比赛由
-- backend/cli/backfill_match_details.py 按范围重新抓取回填。

ALTER TABLE dim_match ADD COLUMN Venue_Capacity INTEGER;
ALTER TABLE dim_match ADD COLUMN Venue_Surface TEXT;
ALTER TABLE dim_match ADD COLUMN Venue_Lat REAL;
ALTER TABLE dim_match ADD COLUMN Venue_Long REAL;
ALTER TABLE dim_match ADD COLUMN Weather_Localized_Key TEXT;
ALTER TABLE dim_match ADD COLUMN Weather_Icon_Code INTEGER;
ALTER TABLE dim_match ADD COLUMN Referee_ID INTEGER;
ALTER TABLE dim_match ADD COLUMN Referee_Country TEXT;
ALTER TABLE dim_match ADD COLUMN Referee_Country_Code TEXT;
ALTER TABLE dim_match ADD COLUMN Referee_Stats_Json TEXT;

ALTER TABLE fact_shotmap ADD COLUMN Is_Own_Goal INTEGER;
