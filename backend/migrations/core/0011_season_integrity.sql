-- 0011_season_integrity.sql — 赛季归属改为派生值(2026-08-25,CLAUDE.md §6.3 事故收口)。
--
-- 背景:dim_match.Season 此前是调用方手填的可写字段,生产 18,056 行里 878 行
-- 标错,且全部错行的标签都是旧硬编码默认值 '2025/2026';除该值外的每一个
-- 标签与"按 (League_ID, Date) 推导"100% 吻合。FotMob 安卓端 Match 模型根本
-- 没有 Season 字段(APK 反编译核实)——赛季由联赛制度 + 日期隐含。本迁移把
-- 这个语义落到存储层:
--   1. dim_league_season_regime:按 effective_from 分版本的联赛赛季制度表
--      (日职 2026-07 真实换制,单一标量表达不了;种子与
--      backend/season_regime.py::REGIME_SEED 保持一致,测试交叉校验);
--   2. dim_match 触发器:写入的 Season 必须等于按制度推导的赛季,否则 ABORT;
--      League_ID 非空且未登记的联赛一律 ABORT(fail closed,逼新联赛先登记;
--      League_ID 为 NULL 的 canonical 占位行放行,见触发器内注释);
--   3. dim_match(League_ID, Season) 索引(此前该表没有任何业务索引,按赛季
--      过滤全是全表扫)。
--
-- 存量不动:触发器只约束新写入,现存 878 行错标由 backend/cli/season_audit.py
-- 只报不改(站长决定),质量门 G12 盯"新增漂移"。
-- 现有表不重建(§5.2);触发器推导逻辑与 backend/season_regime.py::
-- derived_season_sql 同源,tests/backend/test_season_regime.py 行为等价钉住。
--
-- 已知边界(如实声明):推导只看日期,若未来出现"联赛被不可抗力拖进 7 月空档"
-- (如 2019/20 疫情重启)这类制度事件,写入会被如实拒绝——那需要人在制度表里
-- 补一行 effective_from 条目,不允许静默写错(§2.2)。

CREATE TABLE IF NOT EXISTS dim_league_season_regime (
    league_id      INTEGER NOT NULL,
    effective_from TEXT    NOT NULL
                   CHECK (effective_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    season_kind    TEXT    NOT NULL
                   CHECK (season_kind IN ('calendar_year', 'cross_year')),
    cutover_month  INTEGER NOT NULL CHECK (cutover_month BETWEEN 1 AND 12),
    note           TEXT,
    PRIMARY KEY (league_id, effective_from)
);

-- 种子:与 backend/season_regime.py::REGIME_SEED 一字不差(测试校验)。
INSERT OR REPLACE INTO dim_league_season_regime
    (league_id, effective_from, season_kind, cutover_month, note)
VALUES
    (47,    '1900-01-01', 'cross_year',    7, '英超'),
    (48,    '1900-01-01', 'cross_year',    7, '英冠'),
    (53,    '1900-01-01', 'cross_year',    7, '法甲'),
    (54,    '1900-01-01', 'cross_year',    7, '德甲'),
    (55,    '1900-01-01', 'cross_year',    7, '意甲'),
    (57,    '1900-01-01', 'cross_year',    7, '荷甲'),
    (61,    '1900-01-01', 'cross_year',    7, '葡超'),
    (87,    '1900-01-01', 'cross_year',    7, '西甲'),
    (113,   '1900-01-01', 'cross_year',    7, '澳超(南半球 10 月-次年 5 月,同样跨年)'),
    (42,    '1900-01-01', 'cross_year',    7, '欧冠(7 月资格赛属新赛季,month>=7 正确归入)'),
    (73,    '1900-01-01', 'cross_year',    7, '欧联'),
    (10216, '1900-01-01', 'cross_year',    7, '欧协联'),
    (86,    '1900-01-01', 'cross_year',    7, '未登记联赛(历史数据,跨年制实测吻合)'),
    (110,   '1900-01-01', 'cross_year',    7, '未登记联赛(历史数据,跨年制实测吻合)'),
    (140,   '1900-01-01', 'cross_year',    7, '未登记联赛(历史数据,跨年制实测吻合)'),
    (146,   '1900-01-01', 'cross_year',    7, '未登记联赛(历史数据,跨年制实测吻合)'),
    (59,    '1900-01-01', 'calendar_year', 1, '挪威超'),
    (67,    '1900-01-01', 'calendar_year', 1, '瑞典超'),
    (268,   '1900-01-01', 'calendar_year', 1, '巴甲'),
    (9080,  '1900-01-01', 'calendar_year', 1, '韩K联'),
    (223,   '1900-01-01', 'calendar_year', 1, '日职联(自然年,至 2026-06)'),
    (223,   '2026-07-01', 'cross_year',    7, '日职联(2026-07 起秋春制)');

CREATE INDEX IF NOT EXISTS idx_dim_match_league_season
    ON dim_match(League_ID, Season);

-- 触发器推导表达式与 backend/season_regime.py::derived_season_sql(NEW.Date,
-- NEW.League_ID) 逐字符同构(静态拷贝;行为等价由测试钉住,不在运行期拼 SQL)。
DROP TRIGGER IF EXISTS trg_dim_match_season_insert;
CREATE TRIGGER trg_dim_match_season_insert
BEFORE INSERT ON dim_match
FOR EACH ROW
BEGIN
    -- League_ID 为 NULL 的行放行:canonical 身份层(0003)会先落只有
    -- Match_ID 的占位行,联赛/赛季由后续赛程同步补——那类行本来就没有赛季
    -- 可校验;League_ID 非空而未登记才是真正要挡的"新联赛没走登记流程"。
    SELECT CASE
        WHEN NEW.League_ID IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM dim_league_season_regime r
            WHERE r.league_id = NEW.League_ID
        )
        THEN RAISE(ABORT, 'dim_match season guard: league not registered in dim_league_season_regime (CLAUDE.md §6.3)')
    END;
    SELECT CASE
        WHEN NEW.Season IS NOT NULL AND NEW.Date IS NOT NULL AND NEW.Season <> (
            SELECT CASE r.season_kind
                WHEN 'calendar_year' THEN substr(NEW.Date, 1, 4)
                ELSE CASE
                    WHEN CAST(substr(NEW.Date, 6, 2) AS INTEGER) >= r.cutover_month
                        THEN substr(NEW.Date, 1, 4) || '/'
                             || CAST(CAST(substr(NEW.Date, 1, 4) AS INTEGER) + 1 AS TEXT)
                    ELSE CAST(CAST(substr(NEW.Date, 1, 4) AS INTEGER) - 1 AS TEXT)
                             || '/' || substr(NEW.Date, 1, 4)
                END
            END
            FROM dim_league_season_regime r
            WHERE r.league_id = NEW.League_ID AND r.effective_from <= NEW.Date
            ORDER BY r.effective_from DESC LIMIT 1
        )
        THEN RAISE(ABORT, 'dim_match season guard: Season does not match season derived from (League_ID, Date)')
    END;
END;

DROP TRIGGER IF EXISTS trg_dim_match_season_update;
CREATE TRIGGER trg_dim_match_season_update
BEFORE UPDATE OF Season, Date, League_ID ON dim_match
FOR EACH ROW
BEGIN
    -- League_ID 为 NULL 的行放行:canonical 身份层(0003)会先落只有
    -- Match_ID 的占位行,联赛/赛季由后续赛程同步补——那类行本来就没有赛季
    -- 可校验;League_ID 非空而未登记才是真正要挡的"新联赛没走登记流程"。
    SELECT CASE
        WHEN NEW.League_ID IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM dim_league_season_regime r
            WHERE r.league_id = NEW.League_ID
        )
        THEN RAISE(ABORT, 'dim_match season guard: league not registered in dim_league_season_regime (CLAUDE.md §6.3)')
    END;
    SELECT CASE
        WHEN NEW.Season IS NOT NULL AND NEW.Date IS NOT NULL AND NEW.Season <> (
            SELECT CASE r.season_kind
                WHEN 'calendar_year' THEN substr(NEW.Date, 1, 4)
                ELSE CASE
                    WHEN CAST(substr(NEW.Date, 6, 2) AS INTEGER) >= r.cutover_month
                        THEN substr(NEW.Date, 1, 4) || '/'
                             || CAST(CAST(substr(NEW.Date, 1, 4) AS INTEGER) + 1 AS TEXT)
                    ELSE CAST(CAST(substr(NEW.Date, 1, 4) AS INTEGER) - 1 AS TEXT)
                             || '/' || substr(NEW.Date, 1, 4)
                END
            END
            FROM dim_league_season_regime r
            WHERE r.league_id = NEW.League_ID AND r.effective_from <= NEW.Date
            ORDER BY r.effective_from DESC LIMIT 1
        )
        THEN RAISE(ABORT, 'dim_match season guard: Season does not match season derived from (League_ID, Date)')
    END;
END;
