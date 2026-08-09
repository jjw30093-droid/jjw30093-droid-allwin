-- kbisai(春秋直播)赔率变化点(CLAUDE.md §6.2/§6.3):一行 = 一个真实观测到的
-- 变化点,不是"初盘/最新"两槽模型。与 bronze_ng_odds_snap(nowgoal)的关键区别:
--
--   1. provider 列从第一天就有(bronze_ng_odds_snap 没有,是历史遗留问题,
--      本表不重蹈覆辙——即便当前只有 kbisai 一个 provider 写这张表);
--   2. AH/OU 的盘口线(handicap_line)是一等列,不是塞进 JSON 里的次要字段;
--   3. UNIQUE 键包含赔率内容本身(point_hash),不是只看 (match,market,
--      company,时间)——kbisai 真实观测到过"同一个 changeTime 两条不同赔率"
--      (来源在同一秒发布了两次更新),只看时间戳的 UNIQUE 键会静默丢掉其中
--      一条真实数据;
--   4. source_updated_at 存来源自己声明的 changeTime(真实来源时间,不是
--      我们自己观察到的时间)——这是 kbisai matchAllOdds 独有的能力,
--      realtimeMatch_b/futureMatch_b 两个接口不提供来源声明的更新时间,
--      不要把这条经验错误推广到 kbisai 的其它接口。
--
-- 幂等:point_hash 覆盖 handicap_line/三个赔率数值/closed_flag/statusId/
-- goingTime/score 全部字段,加 dup_ordinal 处理"来源在同一次响应里就给了
-- 两条完全字节相同的记录"这种真实边界情况(2016 年老比赛上观察到过)——
-- 重复抓取同一个真实来源条目时,点位内容+dup_ordinal 分配都是确定性的,
-- UNIQUE 冲突会被写入方分类成"重复,跳过",不会话无限增长。
--
-- append-only:UPDATE 触发器在所有连接上都生效;DELETE 触发器只在
-- PRAGMA recursive_triggers=ON 的连接上对 INSERT OR REPLACE 的隐式删除生效
-- (backend/db/connections.py::connect_rw 目前没有开这个 PRAGMA)——对普通
-- DELETE 语句,触发器始终生效。真正的持久保证是 S3 备份
-- (deploy/scripts/backup_sqlite.sh),不是这个触发器。

CREATE TABLE bronze_kbisai_odds_point (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,

  provider           TEXT NOT NULL DEFAULT 'kbisai',
  provider_match_id  TEXT NOT NULL,
  fotmob_match_id    INTEGER,             -- 可空:采集时点未必已确认映射

  market             TEXT NOT NULL CHECK (market IN ('1x2','ah','ou')),
  source_market      TEXT NOT NULL CHECK (source_market IN ('eu','asia','bs')),

  company_id         TEXT NOT NULL,
  company_name       TEXT NOT NULL DEFAULT '',

  -- AH/OU 必须带盘口线,1x2 没有盘口线——两边都不得含糊。
  handicap_line      TEXT
                     CHECK (
                       (market = '1x2' AND handicap_line IS NULL)
                       OR (market IN ('ah','ou') AND handicap_line IS NOT NULL)
                     ),
  odds_home_or_over  TEXT,
  odds_draw          TEXT,                -- 只有 market='1x2' 时有意义
  odds_away_or_under TEXT,
  closed_flag        INTEGER CHECK (closed_flag IN (0,1)),

  source_status_id   INTEGER,             -- 来源原始 statusId,语义未跨接口验证(见 provider 模块)
  going_time         TEXT,                -- 来源原始 goingTime(可能是空字符串)
  score              TEXT,                -- 来源原始比分字符串,如 "0-0"

  market_phase       TEXT NOT NULL DEFAULT 'unknown'
                     CHECK (market_phase IN ('pre_match','in_play','unknown')),

  source_updated_at  TEXT,                -- changeTime → UTC ISO(来源声明时间,可空)
  observed_at        TEXT NOT NULL,       -- 本系统观察到这条记录的时间
  ingested_at        TEXT NOT NULL,
  poll_run_id        TEXT,

  raw_point_json     TEXT NOT NULL,       -- 原始条目(未裁剪),供审计/排查
  point_hash         TEXT NOT NULL,       -- raw_point_json 的稳定 hash(sha256 hex)
  dup_ordinal        INTEGER NOT NULL DEFAULT 0,

  UNIQUE (provider, provider_match_id, market, company_id, source_updated_at,
          point_hash, dup_ordinal)
);

CREATE INDEX idx_kbisai_odds_point_fotmob_match
  ON bronze_kbisai_odds_point (fotmob_match_id);

CREATE INDEX idx_kbisai_odds_point_series
  ON bronze_kbisai_odds_point (provider_match_id, market, company_id, source_updated_at);

CREATE TRIGGER trg_kbisai_odds_point_no_update
BEFORE UPDATE ON bronze_kbisai_odds_point
BEGIN
  SELECT RAISE(ABORT, 'kbisai odds change point is append-only (no update)');
END;

CREATE TRIGGER trg_kbisai_odds_point_no_delete
BEFORE DELETE ON bronze_kbisai_odds_point
BEGIN
  SELECT RAISE(ABORT, 'kbisai odds change point is append-only (no delete)');
END;
