-- 0018: silver_team_season_ratios 加 sample_count 列(2026-09-14,包四第二批,
-- 防线与门将「场均门将扑救超额」)。
--
-- 背景:该指标的公式是 Σ xGOT(射正、非点球、非乌龙) − 非点球失球,按场均展示
-- (denominator_sum 已经承担"赛季累计场次"这个角色)。但真实数据审计确认
-- 被扑救射门 xGOT 缺失率高达 33.15%,方案要求"必须排除缺失而非填 0,并在图上
-- 公示'基于 N 次有 xGOT 的射正'"——这个 N 是"纳入 Σ xGOT 求和的有效射正次数",
-- 与 numerator_sum/denominator_sum/paired_matches 三个既有字段的含义都不同,
-- 不能复用。
--
-- 通用性:命名为 sample_count 而不是 gk_valid_xgot_shots,因为这类"分子分母
-- 之外、专供覆盖率类免责披露用的额外样本量"未来其它指标也可能需要——不是
-- 只给这一个指标开洞。其余既有指标该列恒为 NULL,不是 0(0 是"有效样本数
-- 确实为零"这个真实结果,NULL 才是"这个指标不使用这个字段")。

ALTER TABLE silver_team_season_ratios ADD COLUMN "sample_count" INTEGER;
