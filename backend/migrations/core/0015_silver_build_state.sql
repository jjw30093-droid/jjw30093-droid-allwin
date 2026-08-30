-- 0015: core_silver_build 的按(联赛+赛季)水位状态,替代原来的全库单一水位。
--
-- 背景(CLAUDE.md §6.3,站长真实诊断,2026-08-26):
-- backend/worker/runner.py::_watermark_core_silver_build() 原来的水位信号是
-- `SELECT COUNT(*) FROM dim_match WHERE status='Finish'`(全库标量)——任意
-- 一个联赛有一场球踢完,这个数字就变,水位守卫就判定"有新数据",整个
-- backend/silver/build_silver.py::build_silver() 会对库里出现过的**全部**
-- (League_ID, Season) 分区做一次全量 DELETE+INSERT(实测 30+ 分区、5 秒左右)。
-- K联赛踢完一场会连带把西甲、英超等完全没变化的联赛的 5 张 silver 表重写
-- 一遍,数值不变但 updated_at 全部被拍到最新,导致 updated_at 失去"这个
-- 联赛真的更新过"的含义。
--
-- 本表把水位下沉到 build_silver() 内部、按 (League_ID, Season) 粒度:每个
-- 分区记录"上一次成功构建时看到的该分区 Finish 场次数"(finished_count)。
-- 构建时逐分区比较,数字没变就跳过该分区的 DELETE+INSERT,数字变了才重建
-- 并更新本表。backend/worker/runner.py 里原有的全库级 watermark_fn 不动
-- (它仍然是"整库真的一场都没踢完"时提前跳过整个任务的快速门,与本表是
-- 两层独立的、互不冲突的水位)。
--
-- 主键用 (League_ID, Season) 而不是单纯 League_ID:与 0014(standings_refresh_
-- state)同一条理由——跨赛季状态需要分别保留,不能被新赛季覆盖旧赛季的记录。

CREATE TABLE silver_build_state (
  league_id      INTEGER NOT NULL,
  season         TEXT NOT NULL,
  finished_count INTEGER NOT NULL,
  built_at       TEXT NOT NULL,
  PRIMARY KEY (league_id, season)
);
