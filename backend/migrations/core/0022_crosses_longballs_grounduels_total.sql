-- 0022: fact_player_match_stats 加三列(2026-09-23)。
--
-- 背景:用真实浏览器打开 FotMob 网页版(巴萨 vs 拉辛桑坦德,match_id=5868066),
-- 逐个点开"传球/对抗"两个球员数据子 tab,跟生产库逐列核对时发现——网页上
-- 显示的是分数(如亚马尔"精准传中 0/2"、"精准长传 1/1"、"地面对抗成功 9/16"),
-- 生产库只存了分子。直接拉这场真实比赛的原始 payload 确认三个字段都是
-- fractionWithPercentage:
--   accurate_crosses    {"value":0,"total":2}
--   long_balls_accurate {"value":1,"total":1}
--   ground_duels_won    {"value":9,"total":16}
-- 同一次核对把这名球员全部 fractionWithPercentage 字段扫了一遍(见
-- accurate_passes_total/aerials_won_total/dribbles_succeeded_total 三个
-- 既有先例),确认没有第四个漏网的。_build_stat_lookup 早就在产出这三个
-- "{key}__total" 派生 key,此前没有列去接住,不需要新抓数据。
--
-- 表本身已由 0007 建过骨架,这里不用再重复 CREATE TABLE IF NOT EXISTS,
-- 直接加列。

ALTER TABLE fact_player_match_stats ADD COLUMN "accurate_crosses_total" REAL;
ALTER TABLE fact_player_match_stats ADD COLUMN "long_balls_accurate_total" REAL;
ALTER TABLE fact_player_match_stats ADD COLUMN "ground_duels_won_total" REAL;
