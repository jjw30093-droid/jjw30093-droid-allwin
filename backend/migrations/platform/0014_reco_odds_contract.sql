-- 0014: reco_legs 赔率合约修复(真实数据确认的财务结算 bug)。
--
-- 背景(2026-08-16,已用真实数据确认):settle_slip() 一直把 reco_legs.odds
-- 当十进制赔率直接相乘,但 NowGoal 的 ou/ah/corners_ou 三个市场给的是港赔
-- (HK odds),数值经常 <1(如 0.83、1.03),和十进制赔率(必须 >1)是完全不同
-- 的数字含义(真实样本见 backend/commands/reco_odds_contract.py 顶部注释)。
-- 标准港盘转换:十进制 = 港盘 + 1.0。
--
-- 本迁移只加字段,不重新解释任何既有数据:
-- - 新增 reco_legs.canonical_decimal_odds 作为结算乘法唯一真源,既有行
--   backfill 为它们现有 odds 列的原值(原样照抄,不做 HK→decimal 转换——
--   这些行过去就是被当作十进制直接使用的,已结算的 result/return_units
--   不因这次改动发生任何变化,odds 列本身也完全不变);
-- - 新增 source_odds/odds_format/provider/company_id/company_name/
--   snapshot_ref/observed_at/line/side/payload_hash 溯源字段,全部 nullable,
--   既有行保持 NULL(诚实反映"缺乏真实溯源",不是惩罚);
-- - 新增 entry_type(provenance_bound / legacy_manual),既有行落在默认值
--   legacy_manual;
-- - reco_slips 新增 settle_source(auto/manual),供下一阶段自动结算任务
--   判断"这张单是否已被人工改过,不能覆盖";
-- - reco_slips.result 与 reco_legs.result 的 CHECK 都追加 half_win/half_loss
--   (四分之一盘口半赢半输)。
--
-- SQLite 无法直接 ALTER CHECK / ALTER 已有 CHECK 约束的列,按仓库既有先例
-- (backend/migrations/odds/0008_corners_market.sql)用"建新表 + 拷贝 + 替换"
-- 重建。与 0008 不同的是 reco_legs.slip_id 对 reco_slips(id) 声明了
-- ON DELETE CASCADE——如果先 DROP 旧父表 reco_slips 而旧子表 reco_legs 还在
-- (仍声明 REFERENCES reco_slips),SQLite 在 foreign_keys=ON 时会对旧父表
-- 执行隐式 DELETE 并触发级联,导致旧子表的数据被意外清空。因此重建顺序
-- 必须是:
--   1) 建 reco_slips_new(父)与 reco_legs_new(子,REFERENCES reco_slips_new);
--   2) 先拷贝父表数据到 reco_slips_new,再拷贝子表数据到 reco_legs_new
--      (此时子表 FK 已经能在 reco_slips_new 里找到对应父行,拷贝不会被拒绝);
--   3) 先 DROP 旧子表 reco_legs,再 DROP 旧父表 reco_slips——此时已经没有
--      任何表的 FK 还声明引用字面量 "reco_slips"(新子表引用的是
--      "reco_slips_new"),DROP 旧父表不会触发级联删除;
--   4) 两个 _new 表分别 RENAME 回原名(SQLite ALTER TABLE RENAME 会自动把
--      reco_legs_new 里对 "reco_slips_new" 的 REFERENCES 改写为 "reco_slips")。
-- 该顺序已经用独立的最小化 SQLite 复现脚本验证过(parent/child + ON DELETE
-- CASCADE,按此顺序重建后 integrity_check=ok、foreign_key_check 无违规、
-- 两表行数与内容都不变);反向顺序(先 DROP 父表)已验证会导致子表数据被
-- 静默级联清空,因此不采用。

CREATE TABLE reco_slips_new (
  id            TEXT PRIMARY KEY,            -- UUID
  slip_date     TEXT NOT NULL,               -- 归属自然日(北京时间 YYYY-MM-DD,列表/战绩按日聚合)
  title         TEXT NOT NULL,               -- 如"今日三串一"
  note          TEXT,                        -- 思路说明(可空;§1 文案纪律适用)
  combo_type    TEXT NOT NULL CHECK (combo_type IN ('single','parlay')),
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','published','settled','voided')),
  result        TEXT CHECK (result IN ('win','lose','push','half_win','half_loss')),
  return_units  REAL,                        -- 结算回报(单位;1 单位注)
  published_at  TEXT,
  settled_at    TEXT,
  created_by    TEXT NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  edit_count    INTEGER NOT NULL DEFAULT 0,
  -- 本次结算是自动任务(reco_auto_settle,下一阶段)做的还是人工在 admin 面
  -- 做的;NULL = 从未结算过。下一阶段自动结算任务用它判断"这张单已经被
  -- 人工改过,不能覆盖"。
  settle_source TEXT CHECK (settle_source IS NULL OR settle_source IN ('auto','manual'))
);

CREATE TABLE reco_legs_new (
  id            TEXT PRIMARY KEY,
  slip_id       TEXT NOT NULL REFERENCES reco_slips_new(id) ON DELETE CASCADE,
  match_id      INTEGER,                     -- 可空:允许站外赛事,展示以 match_desc 为准
  match_desc    TEXT NOT NULL,               -- "曼城 vs 阿森纳 08-15 03:00"(展示冗余,防 core 数据缺失)
  market        TEXT NOT NULL,               -- 1x2 / ah / ou / corners_ou / 自由文本(人工内容不锁枚举)
  selection     TEXT NOT NULL,               -- "主胜" / "让-0.5 主" / "大2.5"(展示文案)
  odds          REAL NOT NULL CHECK (odds > 1.0),   -- 展示用十进制赔率(见下方 canonical_decimal_odds)
  result        TEXT CHECK (result IN ('win','lose','push','half_win','half_loss')),
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,

  -- ── 赔率合约溯源(2026-08-16,全部 nullable,不破坏既有行)──────────
  -- 来源原始赔率数值:可能是港盘(<1 常见)也可能是十进制(>1),取决于市场,
  -- 由 backend/commands/reco_odds_contract.py::MARKET_ODDS_FORMAT 按 market
  -- 显式声明,不由数值大小猜测。
  source_odds             REAL,
  -- 'decimal' | 'hk'。由 provider parser 显式声明。
  odds_format             TEXT CHECK (odds_format IS NULL OR odds_format IN ('decimal','hk')),
  -- 真正参与结算乘法的十进制赔率——settle_slip() 只认这个字段,不再直接用
  -- 上面的 odds 列做乘法。provenance_bound 行 = to_canonical_decimal(source_odds,
  -- odds_format) 的计算结果;legacy_manual 行 = 调用方传入的 odds 原值
  -- (既有行 backfill 同规则:原样照抄旧 odds 列,不做任何格式转换)。
  canonical_decimal_odds  REAL,
  provider                TEXT,               -- 如 'nowgoal'
  company_id              TEXT,
  company_name            TEXT,
  -- 稳定引用来源快照:odds.db 里 bronze_ng_odds_snap.id 的十进制字符串形式
  -- (如 "12345")。读取时 int(snapshot_ref) 即为该表主键;来源库固定是
  -- odds.db 的 bronze_ng_odds_snap,本字段不编码库名。
  snapshot_ref            TEXT,
  -- 来源快照的观测时间(bronze_ng_odds_snap.observed_at),不是本行 created_at。
  observed_at             TEXT,
  -- 市场线(大小球/角球大小的线,让球盘的盘口线)。
  line                    REAL,
  -- 程序可读的选边(home/away/draw/over/under 等),与展示用中文 selection
  -- 分开——结算判定要用 side,不能解析中文字符串。
  side                    TEXT,
  -- 来源快照 payload_hash,直接抄 bronze_ng_odds_snap.payload_hash。
  payload_hash            TEXT,
  -- provenance_bound = 绑定了真实 match_id + 真实市场报价(source_odds/
  -- odds_format/snapshot_ref 等都有值);legacy_manual = 手写内容,诚实标注
  -- "缺乏真实溯源"(不是惩罚性标签,手写内容本身仍然可以存在)。既有行
  -- (本迁移之前写入的)全部落在默认值,因为它们确实没有这些溯源字段。
  entry_type              TEXT NOT NULL DEFAULT 'legacy_manual'
                           CHECK (entry_type IN ('provenance_bound','legacy_manual'))
);

INSERT INTO reco_slips_new
  (id, slip_date, title, note, combo_type, status, result, return_units,
   published_at, settled_at, created_by, created_at, updated_at, edit_count,
   settle_source)
SELECT
  id, slip_date, title, note, combo_type, status, result, return_units,
  published_at, settled_at, created_by, created_at, updated_at, edit_count,
  NULL
FROM reco_slips;

-- 既有行 backfill:canonical_decimal_odds 原样照抄旧 odds 列的值,不做任何
-- HK→decimal 重新解释——这些行过去就是被当作十进制直接使用的,已结算的
-- result/return_units 不因这次改动发生任何变化。odds_format 保持 NULL,
-- entry_type 使用列默认值 legacy_manual,其余溯源字段保持 NULL。
INSERT INTO reco_legs_new
  (id, slip_id, match_id, match_desc, market, selection, odds, result,
   sort_order, created_at, canonical_decimal_odds)
SELECT
  id, slip_id, match_id, match_desc, market, selection, odds, result,
  sort_order, created_at, odds
FROM reco_legs;

DROP TABLE reco_legs;
DROP TABLE reco_slips;

ALTER TABLE reco_slips_new RENAME TO reco_slips;
ALTER TABLE reco_legs_new RENAME TO reco_legs;

CREATE INDEX idx_reco_slips_date ON reco_slips(slip_date DESC);
CREATE INDEX idx_reco_slips_status ON reco_slips(status);
CREATE INDEX idx_reco_legs_slip ON reco_legs(slip_id);
