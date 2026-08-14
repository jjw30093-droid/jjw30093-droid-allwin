-- 0010: 商业模式重构第二部分 —— 付费独家推荐板块「每日精选」。
-- 设计:docs/design-reco-board.md(用户已确认 5 个决策点)。
--
-- 关键边界(CLAUDE.md §9.1 适用范围修订,经用户批准):
-- 人工推荐独立建表,不纳入模型预测登记簿;prediction_snapshots 的
-- CHECK/触发器/NOT NULL 保护本迁移不触碰任何一行。
-- 内容与战绩允许管理员修改,但每次修改写 audit_logs + edit_count 留痕,
-- 战绩页明示"人工内容,可修正"——与模型侧"锁定不可改"的表述彻底分开。
--
-- 三段可见性(用户决策):
--   赛前推荐内容(近 30 天窗口)  = 付费 daily_picks(reco:daily)
--   战绩归档(结算后,命中/未中/走水全展示,作废单列不消失)= 登录即可(member 基线,reco:track_record)
--   匿名 = 只有引导登录,无数字
-- 注单固定 1 单位,不展示金额概念(§1 禁止仓位建议)。

CREATE TABLE reco_slips (
  id            TEXT PRIMARY KEY,            -- UUID
  slip_date     TEXT NOT NULL,               -- 归属自然日(北京时间 YYYY-MM-DD,列表/战绩按日聚合)
  title         TEXT NOT NULL,               -- 如"今日三串一"
  note          TEXT,                        -- 思路说明(可空;§1 文案纪律适用)
  combo_type    TEXT NOT NULL CHECK (combo_type IN ('single','parlay')),
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','published','settled','voided')),
  result        TEXT CHECK (result IN ('win','lose','push')),   -- settled 后填
  return_units  REAL,                        -- 结算回报(单位;1 单位注,win=有效赔率乘积,push=1,lose=0)
  published_at  TEXT,
  settled_at    TEXT,
  created_by    TEXT NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  edit_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_reco_slips_date ON reco_slips(slip_date DESC);
CREATE INDEX idx_reco_slips_status ON reco_slips(status);

CREATE TABLE reco_legs (
  id            TEXT PRIMARY KEY,
  slip_id       TEXT NOT NULL REFERENCES reco_slips(id) ON DELETE CASCADE,
  match_id      INTEGER,                     -- 可空:允许站外赛事,展示以 match_desc 为准
  match_desc    TEXT NOT NULL,               -- "曼城 vs 阿森纳 08-15 03:00"(展示冗余,防 core 数据缺失)
  market        TEXT NOT NULL,               -- 1x2 / ah / ou / 自由文本(人工内容不锁枚举)
  selection     TEXT NOT NULL,               -- "主胜" / "让-0.5 主" / "大2.5"
  odds          REAL NOT NULL CHECK (odds > 1.0),
  result        TEXT CHECK (result IN ('win','lose','push')),   -- 命中/未中/走水
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_reco_legs_slip ON reco_legs(slip_id);

-- 「每日精选」plan:定价不写(products 无对应行;购买路径 = 公众号联系 → admin
-- grant / 兑换码)。rank 5 高于 member,展示 plan 取订阅。
INSERT OR IGNORE INTO plans (id, name_zh, description, rank, is_active, created_at) VALUES
  ('daily_picks', '每日精选', '每日人工独家推荐与串关组合,战绩全程公开可查', 5, 1, '2026-08-10T00:00:00Z');

-- 战绩归档登录即可看(member 基线);物化并集无跨行继承(0004 约定),
-- daily_picks 必须显式含 member 全部行 + 两个 reco 行。
INSERT OR IGNORE INTO plan_entitlements (plan_id, entitlement) VALUES
  ('member', 'reco:track_record'),

  ('daily_picks', 'league:epl'),
  ('daily_picks', 'league:lottery'),
  ('daily_picks', 'league:top5'),
  ('daily_picks', 'prediction:top_probability'),
  ('daily_picks', 'prediction:full_wdl'),
  ('daily_picks', 'prediction:score_matrix'),
  ('daily_picks', 'report:deep'),
  ('daily_picks', 'odds:summary_delayed'),
  ('daily_picks', 'odds:history_full'),
  ('daily_picks', 'odds:raw'),
  ('daily_picks', 'export:basic'),
  ('daily_picks', 'export:full'),
  ('daily_picks', 'alert:odds'),
  ('daily_picks', 'reco:daily'),
  ('daily_picks', 'reco:track_record');
