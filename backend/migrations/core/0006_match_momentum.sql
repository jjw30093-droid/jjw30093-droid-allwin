-- 2026-08-23 对照 FotMob 官方安卓包核实:比赛详情 SSR payload 一直带
-- content.momentum.main.data(约 90-95 个 {minute, value} 点,即"势头图"
-- 的逐分钟曲线),此前从未解析。value 是 FotMob 自己算的黑箱综合评分
-- (不是本站可复现的口径——不同于 fact_shotmap 能自己按 xG 重新聚合,这个
-- 数字来源方法论未公开)。真实比赛(5107575)实测用进球事件反向验证过
-- 正负号含义:正值=主队占优,负值=客队占优(63' 主队进球前后 27→65,
-- 90' 客队进球后转负),不是猜测。
--
-- 新表,不需要先建骨架——fact_match_momentum 此前不存在(不同于
-- 0005_shotmap_raw_fields.sql 那种"给已有表加列",这里是纯新表,
-- CREATE TABLE IF NOT EXISTS 对空库和真实库行为一致)。
--
-- 不回填历史比赛——旧场次没有原始 payload 可重新解析,只有 2026-08-23
-- 起新抓取/重新抓取的比赛才会有数据,前端必须按"这场没有势头数据"
-- 诚实降级,不是"没有势头这个概念"。

CREATE TABLE IF NOT EXISTS fact_match_momentum (
    Match_ID INTEGER,
    Minute REAL,
    Value REAL
);

CREATE INDEX IF NOT EXISTS idx_fact_match_momentum_match
    ON fact_match_momentum (Match_ID);
