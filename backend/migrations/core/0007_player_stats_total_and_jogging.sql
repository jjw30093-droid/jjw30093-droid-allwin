-- 2026-08-23 对照 FotMob 官方安卓包核实:
--   1. 球员级分数式字段(如 accurate_passes)原始 payload 是
--      {"value":37,"total":40,"type":"fractionWithPercentage"},此前只取
--      value 丢了 total——"成功传球 37 次"没有分母看不出好坏,37/40 才行。
--      见 backend/fotmob_client.py::_build_stat_lookup 的 __total 约定。
--   2. 体能分组(Physical metrics)实测(欧冠决赛 5205834)带 Jogging 这一档,
--      此前解析器连列都没有(Walking/Running/Sprinting 三档各自独立列,
--      唯独漏了 Jogging)。
--
-- fact_player_match_stats 此前没有任何 core 迁移创建/接管过(与
-- fact_shotmap 同样的情况,表由 backend/schema.py 的 PLAYER_STATS_COLUMNS
-- 在各初始化脚本里现场建表)。真实 allwin.db 里表已存在 → CREATE IF NOT
-- EXISTS 无效果,仅 ALTER 加列;空库(测试 --data-dir 临时目录)→ 先按
-- 加列前的原始骨架建同构表,再加列,保证迁移在任何环境可重复(同
-- 0001/0005 的先例)。
--
-- 全部可空,不回填历史值——旧场次没有原始 payload 可重新解析,留 NULL 是
-- 唯一诚实的选择。

CREATE TABLE IF NOT EXISTS fact_player_match_stats (
    "Match_ID" INTEGER, "Player_ID" TEXT, "Player_Opta_ID" TEXT, "Team_ID" INTEGER,
    "is_goalkeeper" INTEGER, "is_captain" INTEGER, "shirt_number" TEXT,
    "position_id" INTEGER, "usual_position" TEXT, "rating_title" REAL,
    "minutes_played" INTEGER, "player_name" TEXT, "goals" REAL, "assists" REAL,
    "expected_goals" REAL, "expected_assists" REAL, "xg_and_xa" REAL,
    "expected_goals_on_target_variant" REAL, "expected_goals_non_penalty" REAL,
    "ShotsOnTarget" REAL, "ShotsOffTarget" REAL, "shot_accuracy" REAL,
    "blocked_shots" REAL, "big_chance_missed_title" REAL, "shots_woodwork" REAL,
    "missed_penalty" REAL, "shotmap" TEXT, "accurate_passes" REAL,
    "chances_created" REAL, "big_chance_created_team_title" REAL,
    "passes_into_final_third" REAL, "accurate_crosses" REAL,
    "long_balls_accurate" REAL, "touches" REAL, "touches_opp_box" REAL,
    "dispossessed" REAL, "dribbles_succeeded" REAL,
    "matchstats.headers.tackles" REAL, "shot_blocks" REAL, "clearances" REAL,
    "headed_clearance" REAL, "interceptions" REAL, "recoveries" REAL,
    "dribbled_past" REAL, "ground_duels_won" REAL, "aerials_won" REAL,
    "defensive_actions" REAL, "last_man_tackle" REAL,
    "clearance_off_the_line" REAL, "duel_won" REAL, "duel_lost" REAL,
    "fouls" REAL, "was_fouled" REAL, "penalties_won" REAL,
    "conceded_penalties" REAL, "errors_led_to_goal" REAL, "corners" REAL,
    "Offsides" REAL, "owngoal" REAL, "fantasy_points" REAL, "saves" REAL,
    "goals_conceded" REAL, "expected_goals_on_target_faced" REAL,
    "goals_prevented" REAL, "keeper_diving_save" REAL, "saves_inside_box" REAL,
    "keeper_sweeper" REAL, "punches" REAL, "player_throws" REAL,
    "keeper_high_claim" REAL, "saved_penalties" REAL,
    "saved_penalties_in_shootout" REAL, "physical_metrics_topspeed" REAL,
    "physical_metrics_distance_covered" REAL, "physical_metrics_walking" REAL,
    "physical_metrics_running" REAL, "physical_metrics_sprinting" REAL,
    "physical_metrics_number_of_sprints" REAL
);

ALTER TABLE fact_player_match_stats ADD COLUMN accurate_passes_total REAL;
ALTER TABLE fact_player_match_stats ADD COLUMN physical_metrics_jogging REAL;
