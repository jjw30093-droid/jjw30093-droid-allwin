-- 0023_dim_match_sort_kickoff.sql — /api/v1/matches 排序与时间窗口走索引
-- (2026-09-27,只读诊断 + 方案已经站长审阅,见任务对话与
-- docs/current-state.md)。
--
-- 背景:backend/queries/matches.py::list_matches() 的 ORDER BY 此前是计算
-- 表达式 julianday(COALESCE(kickoff_at_utc, Date)),窗口过滤是
-- julianday(kickoff_at_utc) >= / < julianday(?)。SQLite 不给裸表达式建索引
-- 就无法走索引排序——EXPLAIN QUERY PLAN 显示 League_ID 过滤走了既有索引
-- (idx_dim_match_league_season),但排序阶段一律 USE TEMP B-TREE FOR
-- ORDER BY;联赛过滤覆盖当前几乎全部已接入联赛(默认调用/首页 boost 调用)
-- 时,等价于对全表(生产实测 18,470 行)先排序再截 LIMIT。
--
-- 方案:新增 VIRTUAL 生成列 sort_kickoff_utc(值恒等于既有的
-- COALESCE(kickoff_at_utc, Date) 表达式,只是有了可复用的列名,不新增任何
-- 语义、不改变任何一行现有数据的排序键含义)+ 在其上建索引。
-- 用 VIRTUAL 而不是 STORED:SQLite 的 ALTER TABLE ADD COLUMN 不允许给已有
-- 表新增 STORED 生成列(会报 "cannot add a STORED column",本机验证过),
-- 只能新增 VIRTUAL;VIRTUAL 列本身不占存储、不需要重写整表数据,索引仍能
-- 正常建在其上(索引里存的是计算后的值,效果等价于表达式索引)。
--
-- 必须同时 ANALYZE:本机在生产备份只读副本上验证过,索引建好但不 ANALYZE
-- 时,SQLite 对"League_ID IN (17 个值)"这类宽联赛筛选的默认选择性估计不准,
-- 即使新索引已存在也不会被选中(仍然是旧的
-- SEARCH...idx_dim_match_league_season + TEMP B-TREE);跑一次 ANALYZE 后才
-- 会切换成 SCAN...idx_dim_match_sort_kickoff。全表 ANALYZE 一次性成本可
-- 接受(生产 allwin.db 当前 830MB,备份副本上验证 <1s 量级)。后续随数据
-- 持续写入,统计信息会逐渐漂移,是否需要定期 PRAGMA optimize 留给运维另行
-- 决策,不在本迁移范围内。
--
-- 已知边界(如实声明,不在本次改动范围):backend/api/routes_public.py 的
-- /api/v1/matches 端点无论是否传 boost,都无条件传
-- priority_match_ids=analysis_match_ids|odds_match_ids(生产实测约 13,562
-- 场、占 dim_match 总行数 73%),list_matches() 据此在 ORDER BY 前面加一段
-- CASE WHEN Match_ID IN (...) THEN ... END——这段 CASE 引用的是运行时算出
-- 的 ad-hoc 集合,不是任何持久化列,SQLite 无法用索引满足这一层排序,
-- tiers 非空时仍然需要 TEMP B-TREE。本迁移 + 下面查询改动对以下场景有实测
-- 效果:①赛果/finished 视图(这条分支不建 priority CASE);②任何带时间窗口
-- 的调用(WHERE 侧新增的 sort_kickoff_utc 冗余谓词能被规划器识别为索引范围
-- SEEK,把喂给 TEMP B-TREE 的行数从"全表"缩小到"窗口内",首页/默认赛程页
-- 走的正是这条路径)。对完全不带时间窗口、又携带这条 CASE 前缀的调用(如
-- 裸 /api/v1/matches 或赛季间歇期"自动放宽到全部未来赛程")没有改善——这类
-- 调用需要更深的改动(如把 priority 信号物化成持久化列,或按层分别查询再
-- 在应用层合并),留给后续单独决策,不在本迁移范围内。

ALTER TABLE dim_match
  ADD COLUMN sort_kickoff_utc TEXT
  GENERATED ALWAYS AS (COALESCE(kickoff_at_utc, Date)) VIRTUAL;

CREATE INDEX idx_dim_match_sort_kickoff ON dim_match(sort_kickoff_utc, Match_ID);

ANALYZE dim_match;
