-- 0018: 新增「每日公推」板块(2026-09,产品新增,经用户批准)——与现有
-- 「每日精选」并列的完全公开板块,不需要登录、不需要 reco_access_grants
-- 授权。两个板块共用同一套 reco_slips/reco_legs 表和同一套管理端操作
-- 流程(建单/发布/结算/作废),只是多一个板块归属字段。
--
-- 用 ALTER TABLE ADD COLUMN 而不是像 0014 那样重建表:0014 一次要加十几
-- 列并调整约束形状,重建表更清晰;这里只加一列,SQLite 原生支持在
-- ADD COLUMN 上同时声明 NOT NULL + DEFAULT + CHECK,没有必要为一列改动
-- 承担整表重建的搬运风险。
--
-- DEFAULT 'daily_pick' 是本迁移保证"历史数据零变化"的关键:本迁移前已
-- 用只读查询确认真实 data/platform.db(生产,vip-lightsail):reco_slips
-- 现有 21 行(20 settled + 1 voided),全部在本迁移后自动落在 board=
-- 'daily_pick',现有一切按精选口径的查询(daily_slips/track_record_*)
-- 结果不受影响——后续代码改动会显式给这些查询加 WHERE board=
-- 'daily_pick' 过滤,但即便忘记加,默认值也保证不会把历史数据算进新板块。
--
-- 本迁移只加字段和索引,不触碰任何既有行的其它列,不删除任何数据。

ALTER TABLE reco_slips ADD COLUMN board TEXT NOT NULL DEFAULT 'daily_pick'
  CHECK (board IN ('daily_pick','daily_public'));

CREATE INDEX idx_reco_slips_board_date ON reco_slips(board, slip_date);
