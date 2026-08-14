-- 扩展 bronze_ng_odds_snap.market 允许值,接入角球大小球(corners_ou)真实盘口
-- (2026-08-14,站长要求"三条盘口线走真实盘口",真实探测见下方说明):
--
-- 真实探测(2026-08-14,ThorData 越南出口,titan_id=3008154 真实响应):
-- NowGoal 完整可用市场列表(/oddscomp/<id> 页面的 6 个 tab)为:
--   AH Odds / Over/Under Odds / Corners / Correct Score / Euro Handicap /
--   Double Chance —— 没有黄牌/罚牌(yellow card / booking)市场,该网站
--   根本不提供这个盘口,罚牌市场卡继续使用统计参考线,不冒充真实盘口。
--
-- 角球大小球(Corners O/U)是真实存在的市场,取数端点:
--   GET /ajax/soccerajax?type=14&id=<titan_id>&t=28&cid=<company_id>&h=0&r1=0&r2=0&r3=0
--   响应 Data.ou 是长度为 1 的数组(该端点不像主盘面板那样区分初盘/最新,
--   只给"当前"一条),元素 {odds:{u,g,d}} 字段含义与既有 'ou'(大小球)市场
--   完全一致(u=大/over,g=盘口线/line,d=小/under),故复用
--   backend/providers/nowgoal.py::_FIELD_MAP["ou"] 的解析规则,只是
--   market 名称必须与真正的"大小球(总进球)"市场区分,不能混进同一个
--   market='ou' 桶——那是两个完全不同的标的(总进球 vs 总角球数)。
--
-- SQLite 无法 ALTER CHECK,按 0005 迁移建立的标准重建流程迁移,保留全部既有行。

CREATE TABLE bronze_ng_odds_snap_new (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_match_id TEXT NOT NULL,
  market            TEXT NOT NULL CHECK (market IN ('1x2','ah','ou','corners_ou')),
  company_id        TEXT NOT NULL,
  company_name      TEXT NOT NULL DEFAULT '',
  market_phase      TEXT NOT NULL CHECK (market_phase IN ('pre_match','in_play','unknown')),
  payload_json      TEXT NOT NULL,
  payload_hash      TEXT NOT NULL,
  source_updated_at TEXT,
  observed_at       TEXT NOT NULL,
  ingested_at       TEXT NOT NULL,
  poll_run_id       TEXT
);

INSERT INTO bronze_ng_odds_snap_new
  (id, provider_match_id, market, company_id, company_name, market_phase,
   payload_json, payload_hash, source_updated_at, observed_at, ingested_at, poll_run_id)
SELECT id, provider_match_id, market, company_id, company_name, market_phase,
       payload_json, payload_hash, source_updated_at, observed_at, ingested_at, poll_run_id
FROM bronze_ng_odds_snap;

DROP TABLE bronze_ng_odds_snap;
ALTER TABLE bronze_ng_odds_snap_new RENAME TO bronze_ng_odds_snap;
CREATE INDEX idx_ng_snap_series ON bronze_ng_odds_snap(provider_match_id, market, company_id, observed_at);
