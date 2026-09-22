-- 0021: fact_player_match_stats 加 dribbles_succeeded_total 列(2026-09-23)。
--
-- 同 accurate_passes_total(0007)/aerials_won_total(0020)的既有先例:
-- 原始 payload 的"Successful dribbles"本来就是 fractionWithPercentage
-- ({"value":1,"total":3}),_build_stat_lookup 早就在产出
-- "dribbles_succeeded__total" 这个派生 key,此前没有列去接住,不需要新抓
-- 数据——是围绕球员 Pizza/TOP10 排行榜数据审计时顺手发现的第二处同类缺口。
--
-- 表本身已由 0007 建过骨架,这里不用再重复 CREATE TABLE IF NOT EXISTS,
-- 直接加列。

ALTER TABLE fact_player_match_stats ADD COLUMN "dribbles_succeeded_total" REAL;
