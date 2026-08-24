-- 射门落点图轨迹线联动(2026-08-24,复刻 FotMob 点击射门画轨迹线的效果)。
-- 7 个字段已确认存在于 FotMob 原始 shotmap payload,此前未解析——与
-- 0005/0008 同一种"漏解析不是缺权限"缺口。
--
-- Blocked_X/Blocked_Y:封堵点,与 X_Coord/Y_Coord 同一原始球场坐标系,可直接
-- 用于画轨迹线终点。真正被封堵(Is_Blocked=1)的射门里这两列是否确实非空,
-- 本地样本(3 条 Is_Blocked 均为 0)未能验证,前端必须按"任一为 NULL 就不
-- 画线"处理,不得假设必然有值。
--
-- Goal_Crossed_Y/Goal_Crossed_Z、On_Goal_Shot_X/Y/Zoom_Ratio:球门线穿越点,
-- 疑似 FotMob 内部"球门框局部坐标"(注意 Z 是高度,2D 俯视球场图没有这根
-- 轴),不是 105x68 球场坐标系,量纲/原点未经真实数值核实——先存,消费方
-- 不得未经验证直接当球场坐标用。
--
-- 全部可空,不回填历史值——历史比赛可用 backend/cli/reingest_matches.py
-- 按 match_id 补采。

ALTER TABLE fact_shotmap ADD COLUMN Blocked_X REAL;
ALTER TABLE fact_shotmap ADD COLUMN Blocked_Y REAL;
ALTER TABLE fact_shotmap ADD COLUMN Goal_Crossed_Y REAL;
ALTER TABLE fact_shotmap ADD COLUMN Goal_Crossed_Z REAL;
ALTER TABLE fact_shotmap ADD COLUMN On_Goal_Shot_X REAL;
ALTER TABLE fact_shotmap ADD COLUMN On_Goal_Shot_Y REAL;
ALTER TABLE fact_shotmap ADD COLUMN On_Goal_Shot_Zoom_Ratio REAL;
