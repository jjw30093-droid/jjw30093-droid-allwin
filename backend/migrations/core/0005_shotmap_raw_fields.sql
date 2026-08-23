-- 2026-08-23 对照 FotMob 官方安卓包(com.mobilefootie.wc2010 236.17338)逆向
-- 核实:每一脚射门原始 payload(FotMob SSR `content.shotmap.shots[]`)带
-- 29-30 个字段,`backend.fotmob_client.FotMobClient.parse_shotmap_records`
-- 此前只解析 11 个进 fact_shotmap,以下 6 个被丢弃。本地在
-- `fact_match_events.extra_json.shotmapEvent`(进球事件里 FotMob 原样内嵌
-- 的射门对象)实测确认这些字段确实存在于我们已经在下载的 payload 里,
-- 不需要任何新的外部请求——只是解析时没取。
--
-- 补齐后可修的四个已知缺陷(详见 backend/queries/matchup.py 与
-- frontend/components/charts/ShotMapExplorer.tsx 里对应的口径注释):
--   1. "射正"虚高:AttemptSaved 把"门将扑出"和"被后卫封堵"混在一起算,
--      逐次射门口径 7.725/队场 vs 官方 ShotsOnTarget 4.356/队场,仅
--      6.8% 完全吻合(is_blocked 补上后可精确拆分);
--   2. 90+ 分钟射门全部坍缩到第 90 分钟(缺 Minute_Added);
--   3. 禁区内 xG 目前只能靠坐标法近似(97.97% 准),补上
--      Is_From_Inside_Box 后可作为校验信号,不是替代——校验逻辑见
--      backend/worker/(pipeline_gates)的后续改动,本迁移只加列;
--   4. `ingest_match.py` 顶部注释此前称"shotmap 原始数据本身没有稳定的
--      行 id"——不准确,原始对象带 `id`(Shot_ID),此前只是没有落库。
--
-- 全部可空,不回填历史值——旧场次没有原始 payload 可重新解析,留 NULL 是
-- 唯一诚实的选择;真正回填需要重新抓取(网络操作,不在本迁移范围内)。
--
-- fact_shotmap 此前没有任何 core 迁移创建/接管过(表由 backend/schema.py
-- 的 SHOTMAP_COLUMNS 在各初始化脚本里现场建表,不在版本化迁移历史里)。
-- 真实 allwin.db 里表已存在 → CREATE IF NOT EXISTS 无效果,仅 ALTER 加列;
-- 空库(如测试 --data-dir 临时目录)→ 先按加列前的原始 12 列建同构骨架,
-- 再加列,保证迁移在任何环境可重复(同 0001_dim_match_kickoff.sql 的先例)。

CREATE TABLE IF NOT EXISTS fact_shotmap (
    Match_ID INTEGER,
    Player_ID TEXT,
    Team_ID INTEGER,
    Minute INTEGER,
    Period TEXT,
    X_Coord REAL,
    Y_Coord REAL,
    xG REAL,
    xGOT REAL,
    Situation TEXT,
    Outcome TEXT,
    Shot_Type TEXT
);

ALTER TABLE fact_shotmap ADD COLUMN Shot_ID INTEGER;
ALTER TABLE fact_shotmap ADD COLUMN Is_Blocked INTEGER;
ALTER TABLE fact_shotmap ADD COLUMN Is_On_Target INTEGER;
ALTER TABLE fact_shotmap ADD COLUMN Is_From_Inside_Box INTEGER;
ALTER TABLE fact_shotmap ADD COLUMN Minute_Added INTEGER;
ALTER TABLE fact_shotmap ADD COLUMN Keeper_ID TEXT;
