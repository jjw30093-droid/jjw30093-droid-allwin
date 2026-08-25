-- 0012_natural_keys_period_time_hygiene.sql — 数据层收口(2026-08-25,
-- 对照 FotMob APK 数据模型审计后的 P0 项;docs/current-state.md §62)。
--
-- 四件事,全部非破坏性(除两处点名的数据清理):
--
-- 1. 自然键唯一索引:五张事实表的自然键此前只靠 ingest 的 delete-then-insert
--    纪律维持无重复(生产 2026-08-25 实测五个键 0 重复)。CREATE UNIQUE INDEX
--    不重建表(§5.2),把"纪律"升级成"数据库保证"——任何绕过纪律的写路径
--    从此当场报错,而不是悄悄产生重复行。
--
-- 2. 加时段枚举拼写统一:同一个逻辑枚举在 fact_shotmap 里是
--    FirstHalfExtra/SecondHalfExtra、在 fact_team_match_stats 里是
--    FirstExtraHalf/SecondExtraHalf(生产各 28 行)——两表 join 加时段永远
--    零行,只有 match_report.py 的 _PERIOD_ORDER 手工兼容过。统一为 FotMob
--    APK MatchPeriod 枚举拼法(FirstHalfExtra);写侧归一在
--    fotmob_client.parse_team_stats_records。
--
-- 3. 时间戳格式收敛:updated_at 在库里有两种格式并存——20 字符 ISO Z
--    (utc_now_iso)与 19 字符无时区(datetime('now'),SQLite 恒为 UTC,
--    仅缺标记)。dim_team_i18n 一列同时含两种(145+157 行),该列的
--    MAX(updated_at)/ORDER BY 因 'T'(0x54)>' '(0x20) 而语义错误。全部
--    19 字符行按 UTC 语义补全为 ISO Z;写侧各处统一改用 utc_now_iso()。
--    (19 字符值可安全断定为 UTC:全部来自 SQLite datetime('now'),该函数
--    恒返回 UTC;无任何 Python 本地时间写路径,2026-08-25 逐写者核实。)
--
-- 4. 点名清理(仅这两处,备份先行——release 流程迁移前有三库备份):
--    a) fact_player_match_stats.shotmap:声明即死列,生产 440,527 行 100% NULL
--       (解析器从未填过);DROP COLUMN(SQLite 3.35+,生产 3.37.2)。
--    b) fact_league_table (League_ID=47, Season='2024'):60 行全零占位
--       (played=0、字母序站位),跨年联赛不存在 '2024' 这个赛季——
--       是 §6.3 同类错标的季表版本,读取层(queries/matches.py standings)
--       已为绕开它专门写过过滤;删源头。

-- ── 0. 空库骨架(同 0001/0005/0007 先例:真实库表已存在 → 无效果;
--       空库(--data-dir 测试)→ 按 backend/schema.py 同构建表,保证
--       本迁移在任何环境可重复。列定义由脚本从 schema.py 生成,不手抄。 ──
CREATE TABLE IF NOT EXISTS fact_team_match_stats (
    "Match_ID" INTEGER,
    "Team_ID" INTEGER,
    "Period" TEXT,
    "Goals" REAL,
    "extra_json" TEXT
);

CREATE TABLE IF NOT EXISTS fact_league_table (
    "League_ID" INTEGER,
    "Season" TEXT,
    "table_type" TEXT,
    "Team_ID" INTEGER,
    "Team_Name" TEXT,
    "position" INTEGER,
    "played" INTEGER,
    "wins" INTEGER,
    "draws" INTEGER,
    "losses" INTEGER,
    "goals_for" INTEGER,
    "goals_against" INTEGER,
    "goal_diff" INTEGER,
    "points" INTEGER,
    "deduction" INTEGER,
    "qual_color" TEXT,
    "xg" REAL,
    "xg_conceded" REAL,
    "x_points" REAL,
    "x_position" INTEGER,
    "extra_json" TEXT
);

CREATE TABLE IF NOT EXISTS fact_match_lineup (
    "Match_ID" INTEGER,
    "Team_ID" INTEGER,
    "is_home" INTEGER,
    "formation" TEXT,
    "Player_ID" TEXT,
    "player_name" TEXT,
    "shirt_number" TEXT,
    "position_id" INTEGER,
    "usual_position_id" INTEGER,
    "is_starter" INTEGER,
    "is_captain" INTEGER,
    "country_code" TEXT,
    "market_value" INTEGER,
    "rating" REAL,
    "sub_in_time" INTEGER,
    "sub_out_time" INTEGER,
    "extra_json" TEXT
);

CREATE TABLE IF NOT EXISTS dim_team_i18n (
    "Team_ID" INTEGER PRIMARY KEY,
    "name_en" TEXT,
    "name_zh" TEXT,
    "source" TEXT,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS dim_player_i18n (
    "Player_ID" TEXT PRIMARY KEY,
    "name_en" TEXT,
    "name_zh" TEXT,
    "name_zh_short" TEXT,
    "source" TEXT,
    "model" TEXT,
    "confidence" REAL,
    "needs_review" INTEGER,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS int_match_features (
    "match_id" INTEGER PRIMARY KEY,
    "league_id" INTEGER,
    "season" TEXT,
    "match_date" TEXT,
    "home_team_id" INTEGER,
    "away_team_id" INTEGER,
    "home_xg_for_l5" REAL,
    "home_xg_for_l10" REAL,
    "home_xg_against_l5" REAL,
    "home_xg_against_l10" REAL,
    "home_goals_for_l5" REAL,
    "home_goals_for_l10" REAL,
    "home_goals_against_l5" REAL,
    "home_goals_against_l10" REAL,
    "home_shots_for_l5" REAL,
    "home_shots_for_l10" REAL,
    "home_shots_on_target_for_l5" REAL,
    "home_shots_on_target_for_l10" REAL,
    "home_possession_l5" REAL,
    "home_possession_l10" REAL,
    "home_n_matches_l5" INTEGER,
    "home_n_matches_l10" INTEGER,
    "away_xg_for_l5" REAL,
    "away_xg_for_l10" REAL,
    "away_xg_against_l5" REAL,
    "away_xg_against_l10" REAL,
    "away_goals_for_l5" REAL,
    "away_goals_for_l10" REAL,
    "away_goals_against_l5" REAL,
    "away_goals_against_l10" REAL,
    "away_shots_for_l5" REAL,
    "away_shots_for_l10" REAL,
    "away_shots_on_target_for_l5" REAL,
    "away_shots_on_target_for_l10" REAL,
    "away_possession_l5" REAL,
    "away_possession_l10" REAL,
    "away_n_matches_l5" INTEGER,
    "away_n_matches_l10" INTEGER,
    "home_xg_for_home_l5" REAL,
    "home_xg_for_home_l10" REAL,
    "home_xg_against_home_l5" REAL,
    "home_xg_against_home_l10" REAL,
    "home_goals_for_home_l5" REAL,
    "home_goals_for_home_l10" REAL,
    "home_goals_against_home_l5" REAL,
    "home_goals_against_home_l10" REAL,
    "home_shots_for_home_l5" REAL,
    "home_shots_for_home_l10" REAL,
    "home_shots_on_target_for_home_l5" REAL,
    "home_shots_on_target_for_home_l10" REAL,
    "home_possession_home_l5" REAL,
    "home_possession_home_l10" REAL,
    "home_n_matches_home_l5" INTEGER,
    "home_n_matches_home_l10" INTEGER,
    "away_xg_for_away_l5" REAL,
    "away_xg_for_away_l10" REAL,
    "away_xg_against_away_l5" REAL,
    "away_xg_against_away_l10" REAL,
    "away_goals_for_away_l5" REAL,
    "away_goals_for_away_l10" REAL,
    "away_goals_against_away_l5" REAL,
    "away_goals_against_away_l10" REAL,
    "away_shots_for_away_l5" REAL,
    "away_shots_for_away_l10" REAL,
    "away_shots_on_target_for_away_l5" REAL,
    "away_shots_on_target_for_away_l10" REAL,
    "away_possession_away_l5" REAL,
    "away_possession_away_l10" REAL,
    "away_n_matches_away_l5" INTEGER,
    "away_n_matches_away_l10" INTEGER,
    "xg_for_diff_l5" REAL,
    "xg_for_diff_l10" REAL,
    "goals_for_diff_l5" REAL,
    "goals_for_diff_l10" REAL,
    "target_home_goals" INTEGER,
    "target_away_goals" INTEGER,
    "sample_weight" REAL,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS gold_wdl_predictions (
    "match_id" INTEGER PRIMARY KEY,
    "league_id" INTEGER,
    "season" TEXT,
    "lambda_home" REAL,
    "lambda_away" REAL,
    "lambda_home_is_fallback" INTEGER,
    "lambda_away_is_fallback" INTEGER,
    "p_home" REAL,
    "p_draw" REAL,
    "p_away" REAL,
    "calibrated" INTEGER,
    "confidence" TEXT,
    "reason" TEXT,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS silver_team_season_stats (
    "League_ID" INTEGER,
    "Season" TEXT,
    "Team_ID" INTEGER,
    "matches_played" INTEGER,
    "avg_total_shots" REAL,
    "avg_shots_on_target" REAL,
    "avg_possession" REAL,
    "avg_corners" REAL,
    "avg_fouls" REAL,
    "avg_yellow_cards" REAL,
    "avg_red_cards" REAL,
    "avg_expected_goals" REAL,
    "avg_expected_goals_non_penalty" REAL,
    "avg_expected_goals_open_play" REAL,
    "avg_expected_goals_set_play" REAL,
    "avg_expected_goals_on_target" REAL,
    "avg_touches_opp_box" REAL,
    "clean_sheets" INTEGER,
    "btts_matches" INTEGER,
    "btts_pct" REAL,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS silver_league_season_summary (
    "League_ID" INTEGER,
    "Season" TEXT,
    "total_matches" INTEGER,
    "home_win_pct" REAL,
    "draw_pct" REAL,
    "away_win_pct" REAL,
    "btts_pct" REAL,
    "clean_sheet_pct" REAL,
    "avg_total_goals" REAL,
    "home_away_goal_diff" REAL,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS silver_over_under_thresholds (
    "League_ID" INTEGER,
    "Season" TEXT,
    "threshold" REAL,
    "over_count" INTEGER,
    "under_count" INTEGER,
    "over_pct" REAL,
    "under_pct" REAL,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS silver_score_distribution (
    "League_ID" INTEGER,
    "Season" TEXT,
    "home_score" INTEGER,
    "away_score" INTEGER,
    "match_count" INTEGER,
    "pct" REAL,
    "updated_at" TEXT
);

CREATE TABLE IF NOT EXISTS silver_goal_minute_buckets (
    "League_ID" INTEGER,
    "Season" TEXT,
    "bucket" TEXT,
    "goal_count" INTEGER,
    "pct" REAL,
    "updated_at" TEXT
);


-- ── 2. 加时段拼写统一(先改数据,后建唯一索引——改拼写不可能制造重复:
--       同一 (Match_ID, Team_ID) 下新旧拼写从未共存,旧拼写只在本表出现) ──
UPDATE fact_team_match_stats SET Period='FirstHalfExtra'  WHERE Period='FirstExtraHalf';
UPDATE fact_team_match_stats SET Period='SecondHalfExtra' WHERE Period='SecondExtraHalf';

-- ── 3. 时间戳收敛(19 字符 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DDTHH:MM:SSZ') ──
UPDATE dim_team_i18n              SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE dim_player_i18n            SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE int_match_features         SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE gold_wdl_predictions       SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE silver_team_season_stats   SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE silver_league_season_summary SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE silver_over_under_thresholds SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE silver_score_distribution  SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;
UPDATE silver_goal_minute_buckets SET updated_at = replace(updated_at, ' ', 'T') || 'Z' WHERE length(updated_at) = 19;

-- ── 4b. 幽灵赛季占位行 ──
DELETE FROM fact_league_table WHERE League_ID = 47 AND Season = '2024';

-- ── 4a. 死列(骨架建表见 0007;真实库与 0007 骨架都含该列,可直接 DROP) ──
ALTER TABLE fact_player_match_stats DROP COLUMN shotmap;

-- ── 1. 自然键唯一索引(生产 2026-08-25 实测:五个键均 0 重复) ──
-- 建索引前先按"保留最后写入(MAX rowid)"去重:生产实测五个键 0 重复,
-- 这几条 DELETE 在生产影响 0 行;意义在于让本迁移在任何副本上确定性可重放
-- (本地开发库曾被测试夹具污染出重复行——docs/current-state.md §61 记录过,
-- 排练时正是它们让索引创建失败)。保留 MAX(rowid) 与 ingest 的
-- delete-then-insert 语义一致:最后一次写入是最新抓取。
DELETE FROM fact_team_match_stats WHERE rowid NOT IN (
    SELECT MAX(rowid) FROM fact_team_match_stats GROUP BY Match_ID, Team_ID, Period);
DELETE FROM fact_player_match_stats WHERE rowid NOT IN (
    SELECT MAX(rowid) FROM fact_player_match_stats GROUP BY Match_ID, Player_ID);
DELETE FROM fact_match_lineup WHERE rowid NOT IN (
    SELECT MAX(rowid) FROM fact_match_lineup GROUP BY Match_ID, Player_ID);
DELETE FROM fact_shotmap WHERE Shot_ID IS NOT NULL AND rowid NOT IN (
    SELECT MAX(rowid) FROM fact_shotmap WHERE Shot_ID IS NOT NULL GROUP BY Shot_ID);
DELETE FROM fact_league_table WHERE rowid NOT IN (
    SELECT MAX(rowid) FROM fact_league_table GROUP BY League_ID, Season, table_type, Team_ID);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_team_match_stats_natural
    ON fact_team_match_stats(Match_ID, Team_ID, Period);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_player_match_stats_natural
    ON fact_player_match_stats(Match_ID, Player_ID);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_match_lineup_natural
    ON fact_match_lineup(Match_ID, Player_ID);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_shotmap_shot_id
    ON fact_shotmap(Shot_ID) WHERE Shot_ID IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_league_table_natural
    ON fact_league_table(League_ID, Season, table_type, Team_ID);
