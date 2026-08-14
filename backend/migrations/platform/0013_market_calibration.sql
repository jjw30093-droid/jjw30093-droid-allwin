-- 0013: 赛前市场标定结果(backend/eval/calibrate_markets.py 离线回测产物)。
--
-- 背景:赛前页要给"数据倾向"类意见(角球/罚牌/大小球),但单场足球本质上
-- 几乎不可预测——实测多因子回归对"猜均值"这个基准的改善只有 0.5%-2.9%。
-- 唯一站得住脚的做法是分档:按两队赛前可得的历史均值分 5 档,统计每档在
-- 真实历史里的实际命中率。R² 低不等于没有信号——分档命中率可以单调,
-- 这才是"意见"的合法来源。
--
-- 本表是这套离线回测的产物,不是运行时计算结果:
-- - 只能由 backend/eval/calibrate_markets.py 离线写入,API 请求路径绝不
--   计算或写入本表(CLAUDE.md §5.3 禁止请求内跑批);
-- - 是可重建的派生表(类似 silver_*/gold_* 层),不是需要留痕的正式记录——
--   每次重跑用同一组 (market, league_id) 整体替换,不追加历史版本;
-- - monotonic=0(外样本分档不单调)的行,signal_grade 必须是 NULL——
--   前端据此决定"只展示数据面板,不给意见",这条判断不能在前端重新计算,
--   必须是标定阶段就已经做出的判断,写死在这张表里。
-- league_id 用 0 表示"跨联赛合并标定",不用 NULL——SQLite 的 UNIQUE 约束把
-- 每个 NULL 都当成互不相等,league_id IS NULL 的行会完全绕过下面的 UNIQUE
-- 约束,同一个 (market, line, bucket_index) 能被重复插入多行。0 不是合法
-- league_id(全站联赛 id 均为正数),用作哨兵值不会和真实联赛冲突。
CREATE TABLE market_calibration (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  market          TEXT NOT NULL,        -- 'yellow_cards' / 'corners' / 'goals' / ...
  league_id       INTEGER NOT NULL DEFAULT 0,  -- 0 = 跨联赛合并标定
  line            REAL NOT NULL,        -- 盘口线,如 3.5
  bucket_index    INTEGER NOT NULL,     -- 0..bucket_count-1(0 = 预估值最低档)
  bucket_lower    REAL,                 -- 该档预估值下界(第 0 档为 NULL,开区间)
  bucket_upper    REAL,                 -- 该档预估值上界(最高档为 NULL,开区间)
  hit_rate        REAL NOT NULL,        -- 外样本(验证集)里该档的实际过线率,0..1
  sample_size     INTEGER NOT NULL,     -- 外样本里落在该档的比赛数
  signal_grade    TEXT                  -- 三星/两星/一星,外样本不单调时为 NULL
                  CHECK (signal_grade IN ('★★★','★★','★') OR signal_grade IS NULL),
  spread_pp       REAL NOT NULL,        -- 外样本最高档-最低档命中率差(百分点)
  monotonic       INTEGER NOT NULL CHECK (monotonic IN (0,1)),
  train_size      INTEGER NOT NULL,     -- 定档(calibration)集合样本量
  calibrated_at   TEXT NOT NULL,        -- UTC ISO,本次标定运行时间
  UNIQUE (market, league_id, line, bucket_index)
);

CREATE INDEX idx_market_calibration_lookup ON market_calibration(market, league_id, line);
