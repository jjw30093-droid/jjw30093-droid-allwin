-- 0012: 「关键变化」(时间共现)功能整体下架(站长明确决定,2026-09-13,
-- 完整删除,不是简化/降级)。三张表连同 backend/silver/odds_moves.py 的
-- build_odds_moves/build_event_moves/build_cooccurrence、
-- backend/cli/build_odds_silver.py、odds_silver_build worker job、
-- /api/v1/matches/{id}/cooccurrence 端点一起移除。
--
-- 本迁移前用只读查询确认真实 data/odds.db 行数(2026-09-13):
--   silver_odds_moves      1,535,404 行
--   silver_event_moves          3,844 行
--   gold_move_cooccurrence      32,571 行
-- 三张表均非空;仓库全文搜索确认除本功能(routes_public.py 的
-- /cooccurrence 端点、backend/studio/bundle.py 的 cooccurring_events)外,
-- 没有其它代码读写这三张表。DROP TABLE 顺序遵循 FK 方向
-- (gold_move_cooccurrence.odds_move_id/event_move_id 分别 REFERENCES
-- silver_odds_moves(id)/silver_event_moves(id)),先删子表再删父表。
--
-- final_pre_match_snapshot()(backend/silver/odds_moves.py 同文件里的
-- 第四个函数)不属于本功能,读的是 bronze_ng_odds_snap 而非本迁移涉及的
-- 三张表,原样保留,不受影响。

DROP TABLE gold_move_cooccurrence;
DROP TABLE silver_event_moves;
DROP TABLE silver_odds_moves;
