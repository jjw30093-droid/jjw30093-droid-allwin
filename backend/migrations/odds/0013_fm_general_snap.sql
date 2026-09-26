-- 0013: FotMob match_details 的 general 子树 bronze 快照(2026-09-26,第四批 C)。
--
-- 背景:matches 详情抓取只把 payload 解析成 dim_match / fact_* 后就丢弃原始响应
-- (bronze_fm_lineup_snap / bronze_fm_sideline_snap 只存阵容 / 伤停两个裁剪子树)。
-- 2026-08-24 配色事故(teamColors 没有接线,抓了 1.4 万次全部当场丢弃)暴露了这个
-- 缺口:凡是"以后才想用的字段",历史上都只能重新抓取。本表把 general 子树(比赛身份、
-- 联赛/轮次、开球时间、started/finished、teamColors 及其文字色等)原样留存,以后加
-- 字段(如 fontLightMode/fontDarkMode)可以直接重解析,不必再全量重抓。
--
-- 口径:
--   * payload_json = pageProps.general 的稳定 JSON(排序键、紧凑分隔,与其它 bronze
--     快照同一 canonical 形式;内容语义与来源逐字段一致,不裁剪、不改写);
--   * hash-diff 落库:同一场比赛 payload_hash 与上一条相同则不重复写;
--   * 时间戳纪律(CLAUDE.md §6.2):FotMob 不声明来源更新时间 → source_updated_at 恒 NULL,
--     observed_at 为本系统观察时间(同一轮统一),ingested_at 为写库时间;
--   * 只存新抓取的数据,不回填(历史比赛的原始响应本来就没有保存过)。
--
-- 体量:general 子树约 1KB 量级/条(仓库 fixture prematch-5104961 实测 743 字节)。

CREATE TABLE IF NOT EXISTS bronze_fm_general_snap (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id   INTEGER NOT NULL,
  payload_json      TEXT NOT NULL,
  payload_hash      TEXT NOT NULL,
  source_updated_at TEXT,
  observed_at       TEXT NOT NULL,
  ingested_at       TEXT NOT NULL,
  poll_run_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_fm_general_match
  ON bronze_fm_general_snap(fotmob_match_id, observed_at);
