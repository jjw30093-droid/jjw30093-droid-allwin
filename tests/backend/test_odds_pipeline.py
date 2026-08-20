"""P0.7 测试:NowGoal 快照管线(hash-diff 落库 / 实体解析 / moves / 共现 / admin xref)。

core(allwin.db)在生产是只读 Bronze;这里在临时数据目录里造一个最小假 core
(dim_match / dim_team_i18n 子集,列名与真库一致)供实体解析读取。
fixture 数据按旧项目代码审计的格式构造,真实端点未验证(UNVERIFIED)。
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from backend.ingest.entity_resolution import resolve_match, seed_team_aliases
from backend.ingest.odds_snapshots import (
    ingest_lineup_snapshot,
    ingest_odds_records,
    ingest_sideline_snapshot,
    record_source_health,
)
from backend.providers import nowgoal
from backend.silver.odds_moves import (
    _lineup_change_summary,
    build_cooccurrence,
    build_event_moves,
    build_odds_moves,
)

from .authflow import wechat_scan_login

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nowgoal"

ORIGIN = {"Origin": "http://localhost:3000"}

QUERY_DATE = (date.today() + timedelta(days=7)).isoformat()
NEXT_DATE = (date.today() + timedelta(days=8)).isoformat()


def _ng_kickoff(d: str, hour_utc: int = 14) -> str:
    """core kickoff 固定为 {d}T14:00:00Z(UTC 14:00)。NowGoal 日程行 kickoff 字段
    本身就是 UTC(2026-07-21 真实 titan_id=2912218 交叉验证,见
    entity_resolution.py 模块 docstring),此助手生成与之一致的 NowGoal kickoff
    (不做时区转换)。"""
    return f"{d} {hour_utc:02d}:00:00"

# (Match_ID, Date, home_id, home_name, away_id, away_name)
CORE_MATCHES = [
    (9001, QUERY_DATE, 9825, "Arsenal", 8455, "Chelsea"),
    (9002, QUERY_DATE, 8456, "Manchester City", 8650, "Liverpool"),
    (9003, NEXT_DATE, 8668, "Everton", 8654, "West Ham United"),
    # 同队重复对阵(±1 天内两场)→ 制造映射歧义
    (9004, QUERY_DATE, 10261, "Newcastle United", 9879, "Fulham"),
    (9005, NEXT_DATE, 10261, "Newcastle United", 9879, "Fulham"),
    # 2026-08-17 真实发现:巴西等地区球队 NowGoal 名带地域后缀("Internacional RS"/
    # "Remo Belem (PA)"),core 侧别名只有不带后缀的原名——用于模糊 token 子集匹配测试
    (9006, QUERY_DATE, 8887, "Internacional", 8888, "Remo"),
]

CORE_I18N = [
    (9825, "Arsenal", "阿森纳"),
    (8455, "Chelsea", "切尔西"),
    (8456, "Manchester City", "曼城"),
    (8650, "Liverpool", "利物浦"),
]


@pytest.fixture
def core_db(data_dir):
    """在临时 core.db 里建最小 Bronze 子集(仅测试;生产 core 只读)。"""
    conn = connect_rw("core")
    try:
        # core migration 0001 会在空库预建 dim_match 骨架;测试用自己的最小子集,重建之
        conn.execute("DROP TABLE IF EXISTS dim_match")
        conn.execute(
            """CREATE TABLE dim_match (
                   "Match_ID" INTEGER PRIMARY KEY, "Season" TEXT, "League_ID" INTEGER,
                   "Date" TEXT, "Home_Team_ID" INTEGER, "Away_Team_ID" INTEGER,
                   "Home_Team_Name" TEXT, "Away_Team_Name" TEXT, "status" TEXT,
                   "kickoff_at_utc" TEXT, "kickoff_precision" TEXT, "kickoff_source" TEXT)"""
        )
        conn.execute("DROP TABLE IF EXISTS dim_team_i18n")
        conn.execute(
            """CREATE TABLE dim_team_i18n (
                   "Team_ID" INTEGER PRIMARY KEY, "name_en" TEXT, "name_zh" TEXT,
                   "source" TEXT, "updated_at" TEXT)"""
        )
        for mid, d, hid, hname, aid, aname in CORE_MATCHES:
            conn.execute(
                "INSERT INTO dim_match"
                " (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,"
                "  Home_Team_Name, Away_Team_Name, status, kickoff_at_utc,"
                "  kickoff_precision, kickoff_source)"
                " VALUES (?, '2026/2027', 47, ?, ?, ?, ?, ?, 'NotStarted', ?, 'exact', 'fotmob:fixtures')",
                (mid, d, hid, aid, hname, aname, f"{d}T14:00:00Z"),
            )
        for tid, en, zh in CORE_I18N:
            conn.execute(
                "INSERT INTO dim_team_i18n VALUES (?, ?, ?, 'test', '2026-07-19')",
                (tid, en, zh),
            )
        conn.commit()
        yield conn
    finally:
        conn.close()


@pytest.fixture
def odds_conn(data_dir):
    conn = connect_rw("odds")
    try:
        yield conn
    finally:
        conn.close()


def _odds_records():
    payload = json.loads((FIXTURES / "odds_sample.json").read_text(encoding="utf-8"))
    return [nowgoal.normalize_for_inversion(r, False) for r in nowgoal.parse_odds(payload)]


# ── hash-diff:payload 不变不重复落库 ────────────────────────────────────


class TestHashDiff:
    def test_same_payload_ingested_once(self, odds_conn):
        records = _odds_records()
        r1 = ingest_odds_records(odds_conn, "555", records, "2026-07-19T10:00:00Z", "run1", "pre_match")
        assert r1 == {"inserted": 5, "skipped": 0}  # 三家公司 8(3市场)+1+3
        r2 = ingest_odds_records(odds_conn, "555", records, "2026-07-19T10:15:00Z", "run2", "pre_match")
        assert r2 == {"inserted": 0, "skipped": 5}
        n = odds_conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert n == 5

    def test_changed_payload_appends_new_snapshot(self, odds_conn):
        records = _odds_records()
        ingest_odds_records(odds_conn, "555", records, "2026-07-19T10:00:00Z", "run1", "pre_match")
        changed = [dict(r) for r in records]
        target = next(r for r in changed if r["company_id"] == "8" and r["market"] == "1x2")
        target["latest"] = dict(target["latest"]) | {"home": 1.95}
        r2 = ingest_odds_records(odds_conn, "555", changed, "2026-07-19T10:15:00Z", "run2", "pre_match")
        assert r2 == {"inserted": 1, "skipped": 4}
        rows = odds_conn.execute(
            "SELECT observed_at, source_updated_at, poll_run_id FROM bronze_ng_odds_snap"
            " WHERE market='1x2' AND company_id='8' ORDER BY observed_at"
        ).fetchall()
        assert len(rows) == 2
        # 时间戳纪律:NowGoal 不声明来源更新时间 → source_updated_at 恒 NULL
        assert all(r["source_updated_at"] is None for r in rows)
        assert rows[1]["poll_run_id"] == "run2"

    def test_lineup_and_sideline_hash_diff(self, odds_conn):
        snap = {"home": {"team_id": 1, "formation": "4-3-3", "starters": [], "subs": []},
                "away": {"team_id": 2, "formation": "4-4-2", "starters": [], "subs": []}}
        assert ingest_lineup_snapshot(odds_conn, 9001, snap, "2026-07-19T10:00:00Z", "r1")["inserted"] == 1
        assert ingest_lineup_snapshot(odds_conn, 9001, snap, "2026-07-19T10:05:00Z", "r2")["skipped"] == 1
        changed = json.loads(json.dumps(snap))
        changed["home"]["formation"] = "4-2-3-1"
        assert ingest_lineup_snapshot(odds_conn, 9001, changed, "2026-07-19T10:10:00Z", "r3")["inserted"] == 1

        side = {"team_id": 1, "sidelined": []}
        assert ingest_sideline_snapshot(odds_conn, 9001, 1, side, "2026-07-19T10:00:00Z", "r1")["inserted"] == 1
        assert ingest_sideline_snapshot(odds_conn, 9001, 1, side, "2026-07-19T10:05:00Z", "r2")["skipped"] == 1
        # 另一队独立序列,不互相干扰
        assert ingest_sideline_snapshot(odds_conn, 9001, 2, side | {"team_id": 2},
                                        "2026-07-19T10:05:00Z", "r2")["inserted"] == 1

    def test_lineup_snapshot_write_populates_lineup_type_column(self, odds_conn):
        """PIPELINE_REDESIGN_V2 P2:新写入的行必须在落库时直接写 lineup_type 列
        (不是只靠一次性 backfill),用与 extract_lineup_snapshot 相同的
        snapshot["lineup_type"] 取值。"""
        predicted = {
            "lineup_type": "predicted", "source": "enetpulse",
            "home": {"team_id": 1, "formation": "4-3-3", "starters": [], "subs": []},
            "away": {"team_id": 2, "formation": "4-4-2", "starters": [], "subs": []},
        }
        ingest_lineup_snapshot(odds_conn, 9101, predicted, "2026-08-15T06:00:00Z", "r1")
        row = odds_conn.execute(
            "SELECT lineup_type FROM bronze_fm_lineup_snap WHERE fotmob_match_id=9101"
        ).fetchone()
        assert row["lineup_type"] == "predicted"

        last_starting = dict(predicted, lineup_type="lastStarting11", source="lastStartingLineups")
        ingest_lineup_snapshot(odds_conn, 9102, last_starting, "2026-08-15T06:00:00Z", "r1")
        row = odds_conn.execute(
            "SELECT lineup_type FROM bronze_fm_lineup_snap WHERE fotmob_match_id=9102"
        ).fetchone()
        assert row["lineup_type"] == "lastStarting11"

    def test_lineup_snapshot_write_lineup_type_null_when_absent(self, odds_conn):
        """snapshot 没有 lineup_type 键时(旧 payload 形状/该字段本身缺失的观测),
        列必须如实为 NULL,不得编造默认值(如硬编成 'lastStarting11')。"""
        snap = {"home": {"team_id": 1, "formation": "4-3-3", "starters": [], "subs": []},
                "away": {"team_id": 2, "formation": "4-4-2", "starters": [], "subs": []}}
        ingest_lineup_snapshot(odds_conn, 9103, snap, "2026-07-19T10:00:00Z", "r1")
        row = odds_conn.execute(
            "SELECT lineup_type FROM bronze_fm_lineup_snap WHERE fotmob_match_id=9103"
        ).fetchone()
        assert row["lineup_type"] is None


# ── 实体解析 ─────────────────────────────────────────────────────────────


class TestEntityResolution:
    def test_seed_aliases_idempotent(self, odds_conn, core_db):
        added = seed_team_aliases(odds_conn, core_db)
        assert added > 0
        assert seed_team_aliases(odds_conn, core_db) == 0
        row = odds_conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='阿森纳'"
        ).fetchone()
        assert row[0] == 9825

    def test_exact_match_auto_ok(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # 生产 NowGoal 日程行必带主客 provider team ID;auto_ok 要求身份完整(§3.1)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700001", "home_name": "Arsenal", "away_name": "Chelsea",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5001", "provider_away_id": "5002",
        })
        assert result["resolved"] and result["created"]
        assert result["fotmob_match_id"] == 9001
        assert result["confidence"] == 1.0
        assert result["home_away_inverted"] == 0
        assert result["review_status"] == "auto_ok"
        # 绝不静默 verified=1
        row = odds_conn.execute("SELECT verified, method FROM dim_match_xref WHERE id=?",
                                (result["xref_id"],)).fetchone()
        assert row["verified"] == 0 and row["method"] == "auto"

    def test_chinese_alias_and_inverted_direction(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # NowGoal 主客与 FotMob 相反:利物浦(客)列为主
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700002", "home_name": "利物浦", "away_name": "曼城",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5010", "provider_away_id": "5011",
        })
        assert result["fotmob_match_id"] == 9002
        assert result["home_away_inverted"] == 1
        assert result["review_status"] == "auto_ok"

    def test_partial_match_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700003", "home_name": "Everton", "away_name": "Unknown Town",
            "date": NEXT_DATE,
        })
        assert result["fotmob_match_id"] == 9003
        assert result["confidence"] == 0.5
        assert result["review_status"] == "needs_review"

    def test_ambiguous_candidates_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # ±1 天内两场 Newcastle vs Fulham → 两个候选同分 → 不自动通过
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700004", "home_name": "Newcastle United", "away_name": "Fulham",
            "date": QUERY_DATE,
        })
        assert result["confidence"] == 1.0
        assert result["review_status"] == "needs_review"

    def test_no_match_writes_nothing(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700005", "home_name": "Real Madrid", "away_name": "Barcelona",
            "date": QUERY_DATE,
        })
        assert result["resolved"] is False
        n = odds_conn.execute(
            "SELECT COUNT(*) FROM dim_match_xref WHERE provider_match_id='700005'"
        ).fetchone()[0]
        assert n == 0

    def test_fuzzy_token_subset_recall_routes_to_needs_review_not_auto_ok(self, odds_conn, core_db):
        """2026-08-17 真实发现:NowGoal 给巴西等地区球队名加地域后缀
        ('Internacional RS'/'Remo Belem (PA)'),与 core 侧精确别名('internacional'/
        'remo')不完全相同,精确匹配召回不到任何候选,此前直接 resolved=False 且不
        写任何行——不进 needs_review,人工永远看不到这场比赛,赔率也永远抓不到
        (真实复现:2026-08-17 生产上巴甲近一周的比赛有一半没有任何 xref 映射)。
        修复后:token 子集模糊匹配应该召回到候选,但绝不能单靠模糊匹配就 auto_ok
        (confidence 上限 0.7 < AUTO_OK_THRESHOLD=0.9),必须落 needs_review 交人工
        确认,且不得静默创建 team xref。"""
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700010", "home_name": "Internacional RS",
            "away_name": "Remo Belem (PA)",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5020", "provider_away_id": "5021",
        })
        assert result["resolved"] is True, "token 子集模糊匹配应该能召回候选,不再静默丢弃"
        assert result["fotmob_match_id"] == 9006
        assert result["review_status"] == "needs_review"
        assert result["confidence"] < 0.9, "纯模糊匹配绝不能达到 auto_ok 阈值"
        n = odds_conn.execute(
            "SELECT COUNT(*) FROM dim_team_xref WHERE provider_team_id IN ('5020','5021')"
        ).fetchone()[0]
        assert n == 0, "模糊召回不得静默创建 team xref(只有 auto_ok 才允许)"

    def test_pure_single_sided_fuzzy_match_discarded_not_a_false_candidate(self, odds_conn, core_db):
        """2026-08-17 真实生产复现的第二个坑:token 子集模糊匹配上线后,NowGoal
        真实日程行 'Internacional de Bogota'(哥伦比亚球队,与本场无关)vs
        'Dep.Independiente Medellin' 只靠主队一侧"internacional"模糊命中了巴西
        Internacional 的别名,客队完全没有任何信号(0分)——却因为 fwd>0 就被
        写成了 needs_review,抢占了 fotmob_match_id=9006 的 UNIQUE 名额,导致
        之后真正该场次(Internacional RS vs Remo Belem)的正确候选永远因为
        'conflict_existing_xref' 被挡在外面。

        修复:纯单边模糊命中(另一边 0 分、既非精确也非模糊)必须整体丢弃,不
        进 scored,更不能写 needs_review。只有"至少一边精确"或"两边都至少
        模糊命中"才可信。"""
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700012", "home_name": "Internacional de Bogota",
            "away_name": "Dep.Independiente Medellin",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5040", "provider_away_id": "5041",
        })
        assert result["resolved"] is False, "纯单边模糊命中不应该产生任何候选"
        n = odds_conn.execute(
            "SELECT COUNT(*) FROM dim_match_xref WHERE provider_match_id='700012'"
        ).fetchone()[0]
        assert n == 0

        # 关键回归:清掉这个假候选之后,真正的巴西场次仍然能正常走双边模糊召回
        # 拿到 needs_review(不会因为上面那次尝试残留任何占位而被挡住)。
        real = resolve_match(odds_conn, core_db, {
            "titan_id": "700013", "home_name": "Internacional RS",
            "away_name": "Remo Belem (PA)",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5042", "provider_away_id": "5043",
        })
        assert real["resolved"] is True
        assert real["fotmob_match_id"] == 9006
        assert real["review_status"] == "needs_review"

    def test_fuzzy_recall_only_kicks_in_when_exact_match_empty(self, odds_conn, core_db):
        """模糊匹配只是精确匹配为空时的兜底召回,不改变既有精确匹配路径的行为——
        同一批既有 exact_match_auto_ok 等测试必须继续通过;这里额外确认新增的
        巴西场次不会误伤原本就能精确匹配的英超场次。"""
        seed_team_aliases(odds_conn, core_db)
        result = resolve_match(odds_conn, core_db, {
            "titan_id": "700011", "home_name": "Arsenal", "away_name": "Chelsea",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5030", "provider_away_id": "5031",
        })
        assert result["review_status"] == "auto_ok"
        assert result["confidence"] == 1.0

    def test_existing_xref_returned_not_duplicated(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        row = {"titan_id": "700006", "home_name": "Arsenal", "away_name": "Chelsea",
               "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE)}
        first = resolve_match(odds_conn, core_db, row)
        again = resolve_match(odds_conn, core_db, row)
        assert again["created"] is False
        assert again["xref_id"] == first["xref_id"]


def _arsenal_row(titan="710001", ph="5001", pa="5002", kickoff=None):
    return {"titan_id": titan, "home_name": "Arsenal", "away_name": "Chelsea",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE) if kickoff is None else kickoff,
            "provider_home_id": ph, "provider_away_id": pa}


class TestEntityResolutionSafety:
    """P0-B §6.2:auto_ok 需双边 exact kickoff;team xref 冲突显式化、事务一致、不覆盖既有。"""

    def test_1_both_exact_within_tol_unique_auto_ok_writes_team_xref(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "auto_ok"
        team = {t[0]: t[1] for t in odds_conn.execute(
            "SELECT provider_team_id, canonical_team_id FROM dim_team_xref WHERE provider='nowgoal'")}
        assert team == {"5001": 9825, "5002": 8455}   # Arsenal / Chelsea

    def test_2_missing_nowgoal_kickoff_needs_review_no_team_xref(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        row = _arsenal_row()
        row.pop("kickoff")          # NowGoal 侧缺开球时间
        r = resolve_match(odds_conn, core_db, row)
        assert r["review_status"] == "needs_review"
        # diff 无法计算 → kickoff_diff_seconds NULL 且不写 team xref
        xr = odds_conn.execute("SELECT kickoff_diff_seconds FROM dim_match_xref WHERE id=?",
                               (r["xref_id"],)).fetchone()
        assert xr["kickoff_diff_seconds"] is None
        assert odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0] == 0

    def test_3_core_kickoff_not_exact_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # core 侧精度改为 date_only(非 exact)→ 不满足 auto_ok
        core_db.execute("UPDATE dim_match SET kickoff_precision='date_only' WHERE Match_ID=9001")
        core_db.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "needs_review"
        assert odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0] == 0

    def test_4_invalid_kickoff_format_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        r = resolve_match(odds_conn, core_db, _arsenal_row(kickoff="garbage-not-a-time"))
        assert r["review_status"] == "needs_review"

    def test_5_kickoff_diff_over_tolerance_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # NowGoal kickoff UTC 19:00,与 core 14:00Z 差 5 小时 > 30 分钟
        r = resolve_match(odds_conn, core_db, _arsenal_row(kickoff=_ng_kickoff(QUERY_DATE, 19)))
        assert r["review_status"] == "needs_review"

    def test_6_ambiguous_direction_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        r = resolve_match(odds_conn, core_db, {
            "titan_id": "710006", "home_name": "Newcastle United", "away_name": "Fulham",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5020", "provider_away_id": "5021"})
        assert r["review_status"] == "needs_review"

    def test_7_provider_team_id_same_canonical_idempotent_reuse(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # 预置 5001 → 9825(相同 canonical),不算冲突
        odds_conn.execute(
            "INSERT INTO dim_team_xref (provider, provider_team_id, canonical_team_id, confidence,"
            " verified, method, created_at, updated_at)"
            " VALUES ('nowgoal','5001',9825,1.0,0,'auto','2026-07-19T00:00:00Z','2026-07-19T00:00:00Z')")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "auto_ok"
        assert r["team_conflicts"] == []
        n5001 = odds_conn.execute(
            "SELECT COUNT(*) FROM dim_team_xref WHERE provider_team_id='5001'").fetchone()[0]
        assert n5001 == 1   # 幂等,不重复

    def test_8_provider_team_id_different_canonical_conflict_not_auto_ok(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # 预置 5001 → 9999(不同 canonical)→ 冲突
        odds_conn.execute(
            "INSERT INTO dim_team_xref (provider, provider_team_id, canonical_team_id, confidence,"
            " verified, method, created_at, updated_at)"
            " VALUES ('nowgoal','5001',9999,1.0,0,'auto','2026-07-19T00:00:00Z','2026-07-19T00:00:00Z')")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "needs_review"
        assert any(c["provider_team_id"] == "5001" and c["proposed_canonical"] == 9825
                   for c in r["team_conflicts"])
        # 无部分写入:5002 不应被写入
        assert odds_conn.execute(
            "SELECT COUNT(*) FROM dim_team_xref WHERE provider_team_id='5002'").fetchone()[0] == 0
        # 既有冲突行未被改写
        assert odds_conn.execute(
            "SELECT canonical_team_id FROM dim_team_xref WHERE provider_team_id='5001'"
        ).fetchone()[0] == 9999

    def test_9_verified_manual_team_xref_not_overwritten(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # 预置 5001 → 9825 但 verified=1/manual(相同 canonical → 无冲突,但绝不改写)
        odds_conn.execute(
            "INSERT INTO dim_team_xref (provider, provider_team_id, canonical_team_id, confidence,"
            " verified, method, created_at, updated_at)"
            " VALUES ('nowgoal','5001',9825,1.0,1,'manual','2026-07-19T00:00:00Z','2026-07-19T00:00:00Z')")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "auto_ok"
        row = odds_conn.execute(
            "SELECT verified, method FROM dim_team_xref WHERE provider_team_id='5001'").fetchone()
        assert row["verified"] == 1 and row["method"] == "manual"   # 未被自动流程覆盖

    def test_10_confirmed_match_xref_not_overwritten(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        _insert_xref(odds_conn, "710010", 9001, status="confirmed")
        odds_conn.execute("UPDATE dim_match_xref SET verified=1 WHERE provider_match_id='710010'")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row(titan="710010"))
        assert r["created"] is False and r["review_status"] == "confirmed"
        assert odds_conn.execute(
            "SELECT verified FROM dim_match_xref WHERE provider_match_id='710010'").fetchone()[0] == 1

    def test_11_rejected_match_xref_not_revived(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        _insert_xref(odds_conn, "710011", 9001, status="rejected")
        r = resolve_match(odds_conn, core_db, _arsenal_row(titan="710011"))
        assert r["created"] is False and r["review_status"] == "rejected"

    def test_12_inverted_direction_team_xref_correct(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # core 9002:Home=8456(曼城),Away=8650(利物浦);NowGoal 以利物浦为主 → inverted
        r = resolve_match(odds_conn, core_db, {
            "titan_id": "710012", "home_name": "利物浦", "away_name": "曼城",
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": "5010", "provider_away_id": "5011"})
        assert r["review_status"] == "auto_ok" and r["home_away_inverted"] == 1
        team = {t[0]: t[1] for t in odds_conn.execute(
            "SELECT provider_team_id, canonical_team_id FROM dim_team_xref WHERE provider='nowgoal'")}
        assert team["5010"] == 8650   # NowGoal 主(利物浦)→ canonical 利物浦
        assert team["5011"] == 8456   # NowGoal 客(曼城)→ canonical 曼城

    def test_13_conflict_no_partial_write_match_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        odds_conn.execute(
            "INSERT INTO dim_team_xref (provider, provider_team_id, canonical_team_id, confidence,"
            " verified, method, created_at, updated_at)"
            " VALUES ('nowgoal','5002',7777,1.0,0,'auto','2026-07-19T00:00:00Z','2026-07-19T00:00:00Z')")
        odds_conn.commit()
        before = odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0]
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "needs_review"        # 比赛映射不 auto_ok
        # 事务一致:除既有冲突行外无新增 team xref(5001 也没被写)
        assert odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0] == before
        # 比赛 xref 仍登记为 needs_review(供人工处理),不是半成功
        assert odds_conn.execute(
            "SELECT review_status FROM dim_match_xref WHERE provider_match_id='710001'"
        ).fetchone()[0] == "needs_review"

    def test_14_core_source_null_not_auto_ok(self, odds_conn, core_db):
        """1. core kickoff_precision='exact' 但 kickoff_source 为空(NULL)→ provenance
        不完整,不可信任为精确开球时间,即使 kickoff_at_utc 本身带显式时区也不得 auto_ok。"""
        seed_team_aliases(odds_conn, core_db)
        core_db.execute("UPDATE dim_match SET kickoff_source=NULL WHERE Match_ID=9001")
        core_db.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "needs_review"
        assert odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0] == 0

    def test_15_core_naive_datetime_not_auto_ok(self, odds_conn, core_db):
        """2. core kickoff_at_utc 缺显式时区(naive,如 '...T14:00:00' 无 Z/offset)→
        即使 precision='exact' 且来源非空,也不得被当成可信精确时刻,needs_review。"""
        seed_team_aliases(odds_conn, core_db)
        core_db.execute(
            "UPDATE dim_match SET kickoff_at_utc=? WHERE Match_ID=9001", (f"{QUERY_DATE}T14:00:00",)
        )
        core_db.commit()
        r = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r["review_status"] == "needs_review"
        assert odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0] == 0

    def test_16_nowgoal_date_only_kickoff_not_auto_ok(self, odds_conn, core_db):
        """3. NowGoal 日程行 kickoff 只有日期、无真实时间部分 → 不得被
        datetime.fromisoformat 静默当成当天午夜使用,needs_review。"""
        seed_team_aliases(odds_conn, core_db)
        r = resolve_match(odds_conn, core_db, _arsenal_row(kickoff=QUERY_DATE))
        assert r["review_status"] == "needs_review"
        assert odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0] == 0

    def test_17_existing_auto_ok_revalidated_downgrades_on_team_conflict(self, odds_conn, core_db):
        """5. 既有 auto_ok 映射每次被重新遇到都要重新验证——team xref 事后被(其他
        流程)改得与本次蕴含映射冲突 → 原子降级 needs_review,不静默复用旧结论;
        冲突行本身不被自动流程覆盖。"""
        seed_team_aliases(odds_conn, core_db)
        r1 = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r1["review_status"] == "auto_ok"
        odds_conn.execute(
            "UPDATE dim_team_xref SET canonical_team_id=7777"
            " WHERE provider='nowgoal' AND provider_team_id='5001'"
        )
        odds_conn.commit()
        r2 = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r2["created"] is False
        assert r2["review_status"] == "needs_review"
        assert any(c["provider_team_id"] == "5001" for c in r2["team_conflicts"])
        row = odds_conn.execute(
            "SELECT review_status, verified FROM dim_match_xref WHERE provider_match_id='710001'"
        ).fetchone()
        assert row["review_status"] == "needs_review" and row["verified"] == 0
        assert odds_conn.execute(
            "SELECT canonical_team_id FROM dim_team_xref WHERE provider_team_id='5001'"
        ).fetchone()[0] == 7777

    def test_18_existing_auto_ok_revalidated_downgrades_on_kickoff_drift(self, odds_conn, core_db):
        """5b. 既有 auto_ok 映射:canonical 比赛的 kickoff 事后被订正,与 NowGoal 日程
        行差值超容差 → 重新遇到时原子降级 needs_review。"""
        seed_team_aliases(odds_conn, core_db)
        r1 = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r1["review_status"] == "auto_ok"
        core_db.execute(
            "UPDATE dim_match SET kickoff_at_utc=? WHERE Match_ID=9001", (f"{QUERY_DATE}T20:00:00Z",)
        )
        core_db.commit()
        r2 = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r2["created"] is False
        assert r2["review_status"] == "needs_review"
        assert "kickoff_mismatch_or_imprecise" in r2["validation_errors"]
        row = odds_conn.execute(
            "SELECT review_status, verified FROM dim_match_xref WHERE provider_match_id='710001'"
        ).fetchone()
        assert row["review_status"] == "needs_review" and row["verified"] == 0

    def test_19_existing_auto_ok_stays_auto_ok_when_still_consistent(self, odds_conn, core_db):
        """再次遇到依然一致的 auto_ok 映射 → 保持 auto_ok,重新验证不误伤稳定映射。"""
        seed_team_aliases(odds_conn, core_db)
        r1 = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r1["review_status"] == "auto_ok"
        r2 = resolve_match(odds_conn, core_db, _arsenal_row())
        assert r2["created"] is False and r2["review_status"] == "auto_ok"
        row = odds_conn.execute(
            "SELECT review_status FROM dim_match_xref WHERE provider_match_id='710001'"
        ).fetchone()
        assert row["review_status"] == "auto_ok"


def _arsenal_named(titan, home, away, ph="5001", pa="5002"):
    """Arsenal(9825)/Chelsea(8455)对阵 9001,可显式改写队名/主客 provider ID。"""
    return {"titan_id": titan, "home_name": home, "away_name": away,
            "date": QUERY_DATE, "kickoff": _ng_kickoff(QUERY_DATE),
            "provider_home_id": ph, "provider_away_id": pa}


class TestProviderIdentityAndRevalidation:
    """§9 entity(1-10):新映射 provider 身份完整性 + existing auto_ok 队名/方向/ID 重验证。
    全部经公开 resolve_match 与真实 dim_match_xref/dim_team_xref 行为验证。"""

    def _team_count(self, odds_conn):
        return odds_conn.execute("SELECT COUNT(*) FROM dim_team_xref").fetchone()[0]

    # ---- 新映射 provider 身份完整性(§9.1-4) ----
    def test_1_new_duplicate_provider_id_needs_review_zero_team_xref(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        r = resolve_match(odds_conn, core_db, _arsenal_row(ph="dup", pa="dup"))
        assert r["review_status"] == "needs_review"
        assert "provider_team_ids_not_distinct" in r["validation_errors"]
        assert self._team_count(odds_conn) == 0            # 零 team xref,不半映射

    def test_2_new_missing_home_provider_id_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        row = _arsenal_row()
        row.pop("provider_home_id")
        r = resolve_match(odds_conn, core_db, row)
        assert r["review_status"] == "needs_review"
        assert "provider_team_id_missing" in r["validation_errors"]
        assert self._team_count(odds_conn) == 0

    def test_3_new_missing_away_provider_id_needs_review(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        row = _arsenal_row()
        row.pop("provider_away_id")
        r = resolve_match(odds_conn, core_db, row)
        assert r["review_status"] == "needs_review"
        assert "provider_team_id_missing" in r["validation_errors"]
        assert self._team_count(odds_conn) == 0

    def test_4_new_internal_pair_conflict_same_provider_two_canonical(self, odds_conn, core_db):
        """同一 provider ID 在本次 pairs 内指向两个不同 canonical(主客各一)→ 内部矛盾。"""
        seed_team_aliases(odds_conn, core_db)
        r = resolve_match(odds_conn, core_db, _arsenal_row(ph="X9", pa="X9"))
        assert r["review_status"] == "needs_review"
        assert "provider_team_internal_conflict" in r["validation_errors"]
        assert self._team_count(odds_conn) == 0

    # ---- existing auto_ok 队名/方向/ID 重验证(§9.5-9) ----
    def test_5_existing_auto_ok_name_change_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("710050", "Arsenal", "Chelsea")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("710050", "Completely Different", "Wrong Opponent"))
        assert r["created"] is False and r["review_status"] == "needs_review"
        assert "home_team_name_mismatch" in r["validation_errors"]
        row = odds_conn.execute(
            "SELECT review_status, verified FROM dim_match_xref WHERE provider_match_id='710050'"
        ).fetchone()
        assert row["review_status"] == "needs_review" and row["verified"] == 0

    def test_6_existing_auto_ok_home_away_swap_same_inverted_downgrades(self, odds_conn, core_db):
        """主客名称互换但 home_away_inverted 未变 → 两侧队名都与预期 canonical 不符。"""
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("710051", "Arsenal", "Chelsea")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db, _arsenal_named("710051", "Chelsea", "Arsenal"))
        assert r["review_status"] == "needs_review"
        assert "home_team_name_mismatch" in r["validation_errors"]
        assert "away_team_name_mismatch" in r["validation_errors"]

    def test_7a_existing_auto_ok_provider_id_missing_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("710052", "Arsenal", "Chelsea")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        drop = _arsenal_named("710052", "Arsenal", "Chelsea")
        drop.pop("provider_home_id")
        r = resolve_match(odds_conn, core_db, drop)
        assert r["review_status"] == "needs_review"
        assert "provider_team_id_missing" in r["validation_errors"]

    def test_7b_existing_auto_ok_provider_id_duplicate_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("710053", "Arsenal", "Chelsea")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("710053", "Arsenal", "Chelsea", ph="5001", pa="5001"))
        assert r["review_status"] == "needs_review"
        assert "provider_team_ids_not_distinct" in r["validation_errors"]

    def test_8_existing_auto_ok_correct_rerun_stays_auto_ok(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("710054", "Arsenal", "Chelsea")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db, _arsenal_named("710054", "Arsenal", "Chelsea"))
        assert r["created"] is False and r["review_status"] == "auto_ok"

    def test_9a_existing_confirmed_not_downgraded_by_revalidation(self, odds_conn, core_db):
        """confirmed(人工)映射即便队名变化也不被自动降级或改写。"""
        seed_team_aliases(odds_conn, core_db)
        _insert_xref(odds_conn, "710055", 9001, status="confirmed")
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("710055", "Completely Different", "Wrong Opponent"))
        assert r["created"] is False and r["review_status"] == "confirmed"
        assert odds_conn.execute(
            "SELECT review_status FROM dim_match_xref WHERE provider_match_id='710055'"
        ).fetchone()[0] == "confirmed"

    def test_9b_existing_verified_auto_ok_not_downgraded(self, odds_conn, core_db):
        """verified=1 的 auto_ok:重验证短路,人工确认结果不被自动流程覆盖。"""
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("710056", "Arsenal", "Chelsea")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        odds_conn.execute(
            "UPDATE dim_match_xref SET verified=1 WHERE provider_match_id='710056'")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("710056", "Completely Different", "Wrong Opponent"))
        assert r["review_status"] == "auto_ok"          # verified 短路,不降级
        assert odds_conn.execute(
            "SELECT review_status, verified FROM dim_match_xref WHERE provider_match_id='710056'"
        ).fetchone()[0] == "auto_ok"

    # ---- §9.10:冲突均无部分 team xref 写入(新映射路径) ----
    def test_10_all_new_conflicts_write_zero_team_xref(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        for titan, ph, pa in [("710070", "dup", "dup"), ("710071", "5001", "5001")]:
            resolve_match(odds_conn, core_db, _arsenal_row(titan=titan, ph=ph, pa=pa))
        row = _arsenal_row(titan="710072")
        row.pop("provider_away_id")
        resolve_match(odds_conn, core_db, row)
        assert self._team_count(odds_conn) == 0


def _preinsert_team_xref(conn, provider_team_id, canonical, verified=0, method="auto"):
    conn.execute(
        "INSERT INTO dim_team_xref (provider, provider_team_id, canonical_team_id, confidence,"
        " verified, method, created_at, updated_at)"
        " VALUES ('nowgoal', ?, ?, 1.0, ?, ?, '2026-07-19T00:00:00Z', '2026-07-19T00:00:00Z')",
        (str(provider_team_id), canonical, verified, method),
    )
    conn.commit()


class TestTeamXrefExistenceAndAliasGate:
    """P0 收口:existing auto_ok 要求既有 team xref 行**真实存在且指向预期 canonical**;
    新映射最终 auto_ok 必须通过 alias-only 名称/方向验证(历史 provider xref 只做候选召回,
    不单独充当身份证明)。全部经公开 resolve_match + 真实 DB 断言。"""

    def _team_map(self, odds):
        return {t[0]: t[1] for t in odds.execute(
            "SELECT provider_team_id, canonical_team_id FROM dim_team_xref WHERE provider='nowgoal'")}

    def _xref_row(self, odds, titan):
        return odds.execute(
            "SELECT review_status, verified FROM dim_match_xref WHERE provider_match_id=?",
            (titan,)).fetchone()

    # ---- existing auto_ok 缺失 team xref(1-4) ----
    def test_1_existing_auto_ok_delete_home_xref_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("820001", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        odds_conn.execute("DELETE FROM dim_team_xref WHERE provider_team_id='5001'")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db, base)
        assert r["review_status"] == "needs_review"
        assert "provider_team_xref_missing" in r["validation_errors"]
        row = self._xref_row(odds_conn, "820001")
        assert row["review_status"] == "needs_review" and row["verified"] == 0
        assert "5001" not in self._team_map(odds_conn)          # 不自动补回
        assert self._team_map(odds_conn)["5002"] == 8455        # 另一侧未被改写

    def test_2_existing_auto_ok_delete_away_xref_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("820002", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        odds_conn.execute("DELETE FROM dim_team_xref WHERE provider_team_id='5002'")
        odds_conn.commit()
        r = resolve_match(odds_conn, core_db, base)
        assert r["review_status"] == "needs_review"
        assert "provider_team_xref_missing" in r["validation_errors"]
        assert "5002" not in self._team_map(odds_conn)

    def test_3_existing_auto_ok_swap_to_unmapped_ids_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("820003", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("820003", "Arsenal", "Chelsea", ph="6001", pa="6002"))
        assert r["review_status"] == "needs_review"
        assert "provider_team_xref_missing" in r["validation_errors"]
        tm = self._team_map(odds_conn)
        assert "6001" not in tm and "6002" not in tm            # 全新 ID 不被自动登记

    def test_4_existing_auto_ok_one_side_id_change_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("820004", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("820004", "Arsenal", "Chelsea", ph="5001", pa="6002"))
        assert r["review_status"] == "needs_review"
        assert "provider_team_xref_missing" in r["validation_errors"]
        assert "6002" not in self._team_map(odds_conn)

    # ---- 新映射 alias-only 名称门禁(5-7) ----
    def test_5_new_wrong_names_masked_by_provider_xref_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        _preinsert_team_xref(odds_conn, "8001", 9825)          # 历史 xref 命中 canonical home
        _preinsert_team_xref(odds_conn, "8002", 8455)          # 命中 canonical away
        before = len(self._team_map(odds_conn))
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("820005", "ZZZ Nonexistent", "YYY Nonexistent",
                                         ph="8001", pa="8002"))
        assert r["review_status"] == "needs_review"
        assert "home_team_name_mismatch" in r["validation_errors"]
        assert "away_team_name_mismatch" in r["validation_errors"]
        assert len(self._team_map(odds_conn)) == before        # 无新增 team xref

    def test_6_new_swapped_names_with_provider_xref_downgrades(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        _preinsert_team_xref(odds_conn, "8001", 9825)
        _preinsert_team_xref(odds_conn, "8002", 8455)
        before = len(self._team_map(odds_conn))
        # provider xref 指向 forward,但队名互换 → 身份与方向矛盾,不得 auto_ok
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("820006", "Chelsea", "Arsenal", ph="8001", pa="8002"))
        assert r["review_status"] == "needs_review"
        assert len(self._team_map(odds_conn)) == before

    def test_7_new_alias_and_provider_xref_consistent_auto_ok(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        _preinsert_team_xref(odds_conn, "8001", 9825)
        _preinsert_team_xref(odds_conn, "8002", 8455)
        r = resolve_match(odds_conn, core_db,
                          _arsenal_named("820007", "Arsenal", "Chelsea", ph="8001", pa="8002"))
        assert r["review_status"] == "auto_ok"
        tm = self._team_map(odds_conn)
        assert tm["8001"] == 9825 and tm["8002"] == 8455       # 幂等复用,方向正确

    # ---- 保持 / 保护(8-9) ----
    def test_8_correct_existing_auto_ok_rerun_stays(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        base = _arsenal_named("820008", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        r = resolve_match(odds_conn, core_db, base)
        assert r["created"] is False and r["review_status"] == "auto_ok"
        tm = self._team_map(odds_conn)
        assert tm == {"5001": 9825, "5002": 8455}

    def test_9_confirmed_verified_rejected_protected(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # 不同 fotmob_match_id,避开 UNIQUE(provider, fotmob_match_id)
        # confirmed:即便队名/ID 都错也不降级、不改写
        _insert_xref(odds_conn, "820091", 9002, status="confirmed")
        rc = resolve_match(odds_conn, core_db,
                           _arsenal_named("820091", "ZZZ", "YYY", ph="9", pa="9"))
        assert rc["review_status"] == "confirmed"
        # rejected:不被自动复活
        _insert_xref(odds_conn, "820092", 9003, status="rejected")
        rr = resolve_match(odds_conn, core_db, _arsenal_named("820092", "Arsenal", "Chelsea"))
        assert rr["review_status"] == "rejected"
        # verified=1 的 auto_ok:即便删掉 team xref 也短路,不被重验证降级
        base = _arsenal_named("820093", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        odds_conn.execute("UPDATE dim_match_xref SET verified=1 WHERE provider_match_id='820093'")
        odds_conn.execute("DELETE FROM dim_team_xref WHERE provider_team_id='5001'")
        odds_conn.commit()
        rv = resolve_match(odds_conn, core_db, base)
        assert rv["review_status"] == "auto_ok"                # verified 短路,不降级
        assert self._xref_row(odds_conn, "820093")["review_status"] == "auto_ok"

    # ---- 无部分写入 / 不覆盖 manual/verified(10) ----
    def test_10_failure_paths_no_partial_write_or_overwrite(self, odds_conn, core_db):
        seed_team_aliases(odds_conn, core_db)
        # (a) existing auto_ok 删一侧后重验证:剩余行不被改写、缺失行不被补回
        base = _arsenal_named("820101", "Arsenal", "Chelsea", ph="5001", pa="5002")
        assert resolve_match(odds_conn, core_db, base)["review_status"] == "auto_ok"
        odds_conn.execute("DELETE FROM dim_team_xref WHERE provider_team_id='5001'")
        odds_conn.commit()
        resolve_match(odds_conn, core_db, base)
        assert self._team_map(odds_conn) == {"5002": 8455}      # 精确:只剩 5002,未补 5001
        # (b) 新映射错误队名 + 预置 verified/manual team xref:失败路径不写入、不覆盖既有
        _preinsert_team_xref(odds_conn, "8001", 9825, verified=1, method="manual")
        before = self._team_map(odds_conn)
        resolve_match(odds_conn, core_db,
                      _arsenal_named("820102", "ZZZ", "YYY", ph="8001", pa="8888"))
        row = odds_conn.execute(
            "SELECT verified, method FROM dim_team_xref WHERE provider_team_id='8001'").fetchone()
        assert row["verified"] == 1 and row["method"] == "manual"   # manual/verified 未被覆盖
        assert "8888" not in self._team_map(odds_conn)              # 失败路径无部分写入
        assert self._team_map(odds_conn) == before


# ── moves + 共现 ─────────────────────────────────────────────────────────


def _insert_xref(conn, titan_id, fotmob_match_id, status="auto_ok"):
    conn.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, home_away_inverted, confidence,
            verified, method, kickoff_diff_seconds, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 0, 1.0, 0, 'auto', NULL, ?, '2026-07-19T00:00:00Z',
                   '2026-07-19T00:00:00Z')""",
        (fotmob_match_id, titan_id, status),
    )
    conn.commit()


class TestMovesAndCooccurrence:
    T0, T1, T2, T3 = ("2026-07-19T10:00:00Z", "2026-07-19T10:30:00Z",
                      "2026-07-19T10:40:00Z", "2026-07-19T11:30:00Z")

    def _seed_snapshots(self, conn):
        _insert_xref(conn, "555", 9001)
        records = _odds_records()
        ingest_odds_records(conn, "555", records, self.T0, "r1", "pre_match")
        changed = [dict(r) for r in records]
        target = next(r for r in changed if r["company_id"] == "8" and r["market"] == "1x2")
        target["latest"] = dict(target["latest"]) | {"home": 1.95}  # 只动 1x2 的 home
        ingest_odds_records(conn, "555", changed, self.T1, "r2", "pre_match")

        lineup_a = {"home": {"team_id": 9825, "formation": "4-3-3",
                             "starters": [{"id": 10, "name": "A"}], "subs": []},
                    "away": {"team_id": 8455, "formation": "4-4-2", "starters": [], "subs": []}}
        lineup_b = json.loads(json.dumps(lineup_a))
        lineup_b["home"]["formation"] = "4-2-3-1"
        lineup_b["home"]["starters"] = [{"id": 20, "name": "B"}]
        lineup_c = json.loads(json.dumps(lineup_b))
        lineup_c["away"]["formation"] = "3-5-2"
        ingest_lineup_snapshot(conn, 9001, lineup_a, self.T0, "r1")
        ingest_lineup_snapshot(conn, 9001, lineup_b, self.T2, "r2")   # 距赔率变化 600s → 窗口内
        ingest_lineup_snapshot(conn, 9001, lineup_c, self.T3, "r3")   # 距赔率变化 3600s → 窗口外

    def test_odds_moves_field_level_diff(self, odds_conn):
        self._seed_snapshots(odds_conn)
        inserted = build_odds_moves(odds_conn)
        assert inserted == 1   # 只有 1x2.home 变了
        move = odds_conn.execute("SELECT * FROM silver_odds_moves").fetchone()
        assert move["fotmob_match_id"] == 9001
        assert move["market"] == "1x2" and move["field"] == "home"
        assert move["prev_value"] == "2.05" and move["new_value"] == "1.95"
        assert move["moved_at"] == self.T1
        # 幂等
        assert build_odds_moves(odds_conn) == 0

    def test_unmapped_series_produces_no_moves(self, odds_conn):
        records = _odds_records()
        ingest_odds_records(odds_conn, "999", records, self.T0, "r1", "pre_match")
        changed = [dict(r) for r in records]
        changed[0]["latest"] = dict(changed[0]["latest"]) | {"home": 1.5}
        ingest_odds_records(odds_conn, "999", changed, self.T1, "r2", "pre_match")
        assert build_odds_moves(odds_conn) == 0

    def test_event_moves_with_detail(self, odds_conn):
        self._seed_snapshots(odds_conn)
        side_a = {"team_id": 9825, "sidelined": []}
        side_b = {"team_id": 9825,
                  "sidelined": [{"id": 90, "name": "Injured One", "reason": "injury",
                                 "expected_return": None}]}
        ingest_sideline_snapshot(odds_conn, 9001, 9825, side_a, self.T0, "r1")
        ingest_sideline_snapshot(odds_conn, 9001, 9825, side_b, self.T2, "r2")

        inserted = build_event_moves(odds_conn)
        assert inserted == 3   # lineup 两次变化 + sideline 一次
        lineup_move = odds_conn.execute(
            "SELECT * FROM silver_event_moves WHERE event_type='lineup_change'"
            " ORDER BY moved_at LIMIT 1"
        ).fetchone()
        detail = json.loads(lineup_move["detail_json"])
        assert detail["home"]["formation"] == {"prev": "4-3-3", "new": "4-2-3-1"}
        assert detail["home"]["starters_added"][0]["name"] == "B"
        side_move = odds_conn.execute(
            "SELECT * FROM silver_event_moves WHERE event_type='sideline_change'"
        ).fetchone()
        assert json.loads(side_move["detail_json"])["sidelined_added"][0]["name"] == "Injured One"
        # 幂等
        assert build_event_moves(odds_conn) == 0

    def test_cooccurrence_window(self, odds_conn):
        self._seed_snapshots(odds_conn)
        build_odds_moves(odds_conn)
        build_event_moves(odds_conn)
        inserted = build_cooccurrence(odds_conn, window_seconds=900)
        # 赔率变化 10:30;lineup 变化 10:40(600s,窗口内)与 11:30(3600s,窗口外)
        assert inserted == 1
        row = odds_conn.execute("SELECT * FROM gold_move_cooccurrence").fetchone()
        assert row["fotmob_match_id"] == 9001
        assert row["delta_seconds"] == -600
        assert row["window_seconds"] == 900
        # 幂等
        assert build_cooccurrence(odds_conn, window_seconds=900) == 0


# ── 主教练/替补/lineup_type 变化摘要(2026-08-18,Fix 2/3)───────────────


class TestLineupCoachAndSubsChanges:
    T0, T1 = ("2026-07-19T10:00:00Z", "2026-07-19T10:30:00Z")

    @staticmethod
    def _lineup(home_coach=None, away_coach=None, home_starters=None, home_subs=None,
                lineup_type="lastStarting11"):
        return {
            "lineup_type": lineup_type,
            "source": "lastStartingLineups",
            "home": {"team_id": 9825, "formation": "4-3-3", "coach": home_coach,
                      "starters": home_starters or [{"id": 10, "name": "A"}],
                      "subs": home_subs if home_subs is not None else []},
            "away": {"team_id": 8455, "formation": "4-4-2", "coach": away_coach,
                      "starters": [{"id": 40, "name": "D"}], "subs": []},
        }

    def test_coach_only_change_is_a_real_insert_not_a_skip(self, odds_conn):
        """部署后每个"有旧快照且未开球"的比赛,coach 字段第一次出现会让 hash
        变化 ⇒ append-only 多一行——这是有意为之的设计后果并非要防的 bug
        (计划 Fix 2.2:实际规模 ≈0,但机制真实存在,这里把它写成文档性断言)。"""
        r1 = ingest_lineup_snapshot(odds_conn, 9001, self._lineup(home_coach=None), self.T0, "r1")
        assert r1 == {"inserted": 1, "skipped": 0}
        r2 = ingest_lineup_snapshot(
            odds_conn, 9001, self._lineup(home_coach={"id": 7, "name": "Diego Simeone"}),
            self.T1, "r2",
        )
        assert r2 == {"inserted": 1, "skipped": 0}

    def test_coach_change_emits_a_described_lineup_change(self):
        """两条快照都有可用教练、只有 id 不同 → 真正的换帅,必须描述出来。"""
        prev = json.dumps(self._lineup(home_coach={"id": 1, "name": "A教练"}))
        new = json.dumps(self._lineup(home_coach={"id": 2, "name": "B教练"}))
        detail = _lineup_change_summary(prev, new)
        assert detail["home"]["coach"] == {"prev": "A教练", "new": "B教练"}

    def test_first_time_coach_observation_is_not_a_lineup_change(self):
        """守卫必须基于值不是键:prev coach=None、new 首次给出教练,其余不变 →
        不能报"换帅"(我们无法区分"来源这次才给"与"真的换人",也无法区分
        "刚开始采集这个字段"与"当时确实没有教练")。"""
        prev = json.dumps(self._lineup(home_coach=None))
        new = json.dumps(self._lineup(home_coach={"id": 7, "name": "Diego Simeone"}))
        assert _lineup_change_summary(prev, new) == {}

    def test_first_time_coach_observation_writes_no_lineup_change_row(self, odds_conn):
        """端到端镜像上一条:hash 变了(coach 从 None → 有值)但摘要描述不出
        差异 → _build_event_moves_for 的 `if not detail: continue` 必须挡住
        这一行,不写一条 detail_json='{}' 的空壳 lineup_change(它会被"同期
        事件"渲染成一句我们支持不了的"观察到阵容变化",§2.2 违规)。"""
        ingest_lineup_snapshot(odds_conn, 9001, self._lineup(home_coach=None), self.T0, "r1")
        ingest_lineup_snapshot(
            odds_conn, 9001, self._lineup(home_coach={"id": 7, "name": "Diego Simeone"}),
            self.T1, "r2",
        )
        inserted = build_event_moves(odds_conn)
        assert inserted == 0
        n = odds_conn.execute(
            "SELECT COUNT(*) FROM silver_event_moves WHERE event_type='lineup_change'"
        ).fetchone()[0]
        assert n == 0

    def test_subs_only_change_is_described(self):
        """只有替补名单变化 → 必须描述出来,否则会被上面的"空 detail 不落库"
        守卫连同一起吃掉(C3 的守卫与本条必须同时实现)。"""
        prev = json.dumps(self._lineup(home_subs=[{"id": 30, "name": "C"}]))
        new = json.dumps(self._lineup(home_subs=[{"id": 31, "name": "E"}]))
        detail = _lineup_change_summary(prev, new)
        assert detail["home"]["subs_added"] == [{"id": "31", "name": "E"}]
        assert detail["home"]["subs_removed"] == [{"id": "30", "name": "C"}]

    def test_lineup_type_flip_is_described(self):
        """lastStarting11 → predicted,其余不变 → 必须描述出来(同样是 C3 的
        空 detail 守卫必须留活口的另一种变化)。"""
        prev = json.dumps(self._lineup(lineup_type="lastStarting11"))
        new = json.dumps(self._lineup(lineup_type="predicted"))
        detail = _lineup_change_summary(prev, new)
        assert detail["lineup_type"] == {"prev": "lastStarting11", "new": "predicted"}


# ── source_health ────────────────────────────────────────────────────────


class TestSourceHealth:
    def test_append_only(self, odds_conn):
        record_source_health(odds_conn, "nowgoal", ok=True, latency_ms=120)
        record_source_health(odds_conn, "nowgoal", ok=False, error_summary="WAF 拦截")
        rows = odds_conn.execute(
            "SELECT ok, error_summary FROM source_health WHERE source='nowgoal' ORDER BY id"
        ).fetchall()
        assert [r["ok"] for r in rows] == [1, 0]
        assert rows[0]["error_summary"] is None   # 失败不覆盖成功记录


# ── CLI 离线单轮采集(端到端) ────────────────────────────────────────────


def _js_date_tuple(date_str: str, hour_utc: int = 14) -> str:
    """core kickoff 14:00Z 同日;JS Date 元组月份 0 基。NowGoal 行内 kickoff 字段
    本身就是 UTC(见 _ng_kickoff 说明),不做时区转换。"""
    y, m, d = date_str.split("-")
    return f"{int(y)},{int(m) - 1},{int(d)},{hour_utc:02d},00,00"


def _dynamic_poll_fixture(tmp_path, kickoff_hour_utc: int = 14) -> Path:
    """把静态 fixture 的开球元组改写成与 core kickoff 一致(或指定偏移)的动态版本。

    实体解析现在做 kickoff 差值校验(±30min),静态旧日期会被正确降级
    needs_review——所以正常链路测试必须喂一致的开球时间。
    """
    fixture = json.loads((FIXTURES / "poll_fixture.json").read_text(encoding="utf-8"))
    sched = fixture["schedule_data"]
    sched = sched.replace("2026,7,15,19,30,00", _js_date_tuple(QUERY_DATE, kickoff_hour_utc))
    sched = sched.replace("2026,7,15,22,00,00", _js_date_tuple(QUERY_DATE, kickoff_hour_utc))
    sched = sched.replace("2026,7,15,20,00,00", _js_date_tuple(NEXT_DATE, kickoff_hour_utc))
    fixture["schedule_data"] = sched
    p = tmp_path / "poll_fixture_dynamic.json"
    p.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return p


class TestPollCliOffline:
    def test_offline_poll_end_to_end(self, odds_conn, core_db, capsys, tmp_path):
        from backend.cli.poll_nowgoal import main

        rc = main(["--date", QUERY_DATE, "--offline-fixture",
                   str(_dynamic_poll_fixture(tmp_path))])
        assert rc == 0
        out = capsys.readouterr().out
        assert "日程行: 3" in out

        xrefs = {r["provider_match_id"]: r for r in odds_conn.execute(
            "SELECT * FROM dim_match_xref").fetchall()}
        assert xrefs["3001001"]["review_status"] == "auto_ok"
        assert xrefs["3001001"]["home_away_inverted"] == 0
        assert xrefs["3001002"]["review_status"] == "auto_ok"
        assert xrefs["3001002"]["home_away_inverted"] == 1   # Liverpool 列主 → 反转
        assert xrefs["3001003"]["review_status"] == "needs_review"

        # 球队级 xref:auto_ok 映射写入 dim_team_xref(反转方向已换算);needs_review 不写
        team_xrefs = {r["provider_team_id"]: r["canonical_team_id"] for r in odds_conn.execute(
            "SELECT provider_team_id, canonical_team_id FROM dim_team_xref WHERE provider='nowgoal'")}
        assert team_xrefs["9825"] == 9825 and team_xrefs["8455"] == 8455    # 3001001 正向
        assert team_xrefs["8650"] == 8650 and team_xrefs["8456"] == 8456    # 3001002 反转换算
        assert "8668" not in team_xrefs                                     # needs_review 不登记

        # needs_review 不抓赔率;已映射两场各 2 市场
        snaps = odds_conn.execute(
            "SELECT provider_match_id, market, market_phase, payload_json"
            " FROM bronze_ng_odds_snap ORDER BY provider_match_id, market"
        ).fetchall()
        assert {s["provider_match_id"] for s in snaps} == {"3001001", "3001002"}
        assert all(s["market_phase"] == "pre_match" for s in snaps)

        # 反转归一:3001002 的 AH 双边交换 + 线取负(fixture 原始 line=0.25)
        ah = next(s for s in snaps
                  if s["provider_match_id"] == "3001002" and s["market"] == "ah")
        payload = json.loads(ah["payload_json"])
        assert payload["initial"] == {"home": 0.90, "line": -0.25, "away": 0.98}

        health = odds_conn.execute(
            "SELECT source, ok FROM source_health ORDER BY id").fetchall()
        assert ("nowgoal", 1) in [(h["source"], h["ok"]) for h in health]

        # 第二轮同 fixture → 全部 hash 未变跳过,不重复落库
        rc2 = main(["--date", QUERY_DATE, "--offline-fixture",
                    str(_dynamic_poll_fixture(tmp_path))])
        assert rc2 == 0
        n = odds_conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert n == len(snaps)

    def test_kickoff_mismatch_degrades_to_needs_review(self, odds_conn, core_db, tmp_path):
        """安全校验:NowGoal 开球时间与 core 精确 kickoff 相差 >30min → 不得 auto_ok。"""
        from backend.cli.poll_nowgoal import main

        # NowGoal kickoff UTC 19:00 vs core kickoff 14:00Z → 差 5 小时
        rc = main(["--date", QUERY_DATE, "--offline-fixture",
                   str(_dynamic_poll_fixture(tmp_path, kickoff_hour_utc=19))])
        assert rc == 0
        statuses = {r["provider_match_id"]: r["review_status"] for r in odds_conn.execute(
            "SELECT provider_match_id, review_status FROM dim_match_xref")}
        assert statuses["3001001"] == "needs_review"
        assert statuses["3001002"] == "needs_review"
        # needs_review 不抓赔率
        n = odds_conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
        assert n == 0


# ── CLI 离线单轮采集:角球大小盘(2026-08-14 新增,corners_ou 真实盘口) ──────


def _fixture_with_corners(tmp_path, *, corners_by_titan: dict) -> Path:
    """在 poll_fixture.json 基础上加 "corners" 字段(fixture["corners"][titan_id][cid]
    = type=14&t=28 原始 payload),供 _fetch_corner_odds_for_companies() 离线模式读取。"""
    fixture = json.loads((FIXTURES / "poll_fixture.json").read_text(encoding="utf-8"))
    sched = fixture["schedule_data"]
    sched = sched.replace("2026,7,15,19,30,00", _js_date_tuple(QUERY_DATE))
    sched = sched.replace("2026,7,15,22,00,00", _js_date_tuple(QUERY_DATE))
    sched = sched.replace("2026,7,15,20,00,00", _js_date_tuple(NEXT_DATE))
    fixture["schedule_data"] = sched
    fixture["corners"] = corners_by_titan
    p = tmp_path / "poll_fixture_corners.json"
    p.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return p


class TestPollCliCornerOdds:
    def _corner_payload(self) -> dict:
        return json.loads((FIXTURES / "corner_odds_sample.json").read_text(encoding="utf-8"))

    def test_offline_poll_ingests_corner_odds_only_for_companies_with_fixture_data(
        self, odds_conn, core_db, tmp_path
    ):
        """poll_fixture.json 主盘只有 cid=8(Bet365)一家公司;corners fixture 只给
        3001001 造了数据——验证公司范围不额外扩大、比赛范围不额外扩大(3001002 主盘
        成功但没有对应 corners fixture,不应凭空生成一条角球盘行)。"""
        from backend.cli.poll_nowgoal import main

        fixture_path = _fixture_with_corners(
            tmp_path, corners_by_titan={"3001001": {"8": self._corner_payload()}}
        )
        rc = main(["--date", QUERY_DATE, "--offline-fixture", str(fixture_path)])
        assert rc == 0

        corner_rows = odds_conn.execute(
            "SELECT provider_match_id, company_id, market_phase, payload_json"
            " FROM bronze_ng_odds_snap WHERE market='corners_ou'"
        ).fetchall()
        assert [r["provider_match_id"] for r in corner_rows] == ["3001001"]
        assert corner_rows[0]["company_id"] == "8"
        assert corner_rows[0]["market_phase"] == "pre_match"
        payload = json.loads(corner_rows[0]["payload_json"])
        assert payload["initial"] is None   # 角球盘没有初盘,不能假装有
        assert payload["latest"] == {"over": 0.9, "line": 10.5, "under": 0.9}

        # 主盘(1x2/ah)两场各 2 市场照常落库,不受角球盘新增逻辑影响。
        main_count = odds_conn.execute(
            "SELECT COUNT(*) FROM bronze_ng_odds_snap WHERE market IN ('1x2','ah')"
        ).fetchone()[0]
        assert main_count == 4

    def test_offline_poll_corner_odds_hash_diff_dedup_on_rerun(self, odds_conn, core_db, tmp_path):
        from backend.cli.poll_nowgoal import main

        fixture_path = _fixture_with_corners(
            tmp_path, corners_by_titan={"3001001": {"8": self._corner_payload()}}
        )
        rc1 = main(["--date", QUERY_DATE, "--offline-fixture", str(fixture_path)])
        rc2 = main(["--date", QUERY_DATE, "--offline-fixture", str(fixture_path)])
        assert rc1 == 0 and rc2 == 0
        n = odds_conn.execute(
            "SELECT COUNT(*) FROM bronze_ng_odds_snap WHERE market='corners_ou'"
        ).fetchone()[0]
        assert n == 1   # payload 未变,第二轮 hash-diff 跳过,不重复落库

    def test_corner_fetch_failure_does_not_fail_already_ingested_main_markets(
        self, odds_conn, core_db, tmp_path, monkeypatch
    ):
        """角球盘解析炸了(模拟真实抓取/解析异常):主盘(1x2/ah)已经成功落库的
        结果不能被撤销或标记失败,只在 summary["failures"] 里单独记一条。"""
        from backend.cli import poll_nowgoal

        def boom(*_args, **_kwargs):
            raise RuntimeError("角球盘解析炸了")

        monkeypatch.setattr(poll_nowgoal.nowgoal, "parse_corner_odds", boom)

        fixture_path = _fixture_with_corners(
            tmp_path, corners_by_titan={"3001001": {"8": self._corner_payload()}}
        )
        summary = poll_nowgoal.run_poll(QUERY_DATE, offline_fixture=str(fixture_path))

        assert summary["odds_polled"] == 2   # 两场主盘照常抓完,不因角球盘炸了少抓
        assert any(f.startswith("corners titan_id=3001001") for f in summary["failures"])
        main_count = odds_conn.execute(
            "SELECT COUNT(*) FROM bronze_ng_odds_snap WHERE market IN ('1x2','ah')"
        ).fetchone()[0]
        assert main_count == 4
        corner_count = odds_conn.execute(
            "SELECT COUNT(*) FROM bronze_ng_odds_snap WHERE market='corners_ou'"
        ).fetchone()[0]
        assert corner_count == 0


class TestFetchCornerOddsForCompanies:
    """直接单测 _fetch_corner_odds_for_companies() 的公司范围限定/失败隔离/WAF 透传
    (离线 fixture 路径已经在 TestPollCliCornerOdds 里走过完整 CLI 覆盖;这里覆盖
    非 fixture 的真实网络分支,mock 掉 nowgoal.fetch_corner_odds 本身)。"""

    def test_derives_target_companies_from_main_panel_and_dedups(self, monkeypatch):
        from backend.cli import poll_nowgoal

        seen_cids = []

        def fake_fetch(_titan_id, cid, name):
            seen_cids.append(cid)
            return {"market": "corners_ou", "company_id": cid, "company_name": name,
                    "initial": None, "latest": {"over": 0.9, "line": 9.5, "under": 0.9}}

        monkeypatch.setattr(poll_nowgoal.nowgoal, "fetch_corner_odds", fake_fetch)
        main_records = [
            {"market": "1x2", "company_id": "8", "company_name": "Bet365"},
            {"market": "ah", "company_id": "8", "company_name": "Bet365"},   # 同公司第二市场,不重复抓
            {"market": "1x2", "company_id": "3", "company_name": "皇冠"},
        ]
        out = poll_nowgoal._fetch_corner_odds_for_companies(None, "999", main_records)
        assert sorted(seen_cids) == ["3", "8"]
        assert len(out) == 2

    def test_single_company_failure_is_isolated_from_other_companies(self, monkeypatch):
        from backend.cli import poll_nowgoal

        def fake_fetch(_titan_id, cid, name):
            if cid == "8":
                raise poll_nowgoal.nowgoal.NowGoalError("超时")
            return {"market": "corners_ou", "company_id": cid, "company_name": name,
                    "initial": None, "latest": {"over": 0.9, "line": 9.5, "under": 0.9}}

        monkeypatch.setattr(poll_nowgoal.nowgoal, "fetch_corner_odds", fake_fetch)
        main_records = [
            {"market": "1x2", "company_id": "8", "company_name": "Bet365"},
            {"market": "1x2", "company_id": "3", "company_name": "皇冠"},
        ]
        out = poll_nowgoal._fetch_corner_odds_for_companies(None, "999", main_records)
        assert [r["company_id"] for r in out] == ["3"]

    def test_company_with_no_corner_market_returns_none_and_is_skipped(self, monkeypatch):
        """该公司暂无角球盘(ErrCode 非 0 / Data.ou 为空)时 fetch_corner_odds 返回
        None——不是 failure,不应出现在结果里,也不应抛异常。"""
        from backend.cli import poll_nowgoal

        monkeypatch.setattr(poll_nowgoal.nowgoal, "fetch_corner_odds", lambda *a, **k: None)
        main_records = [{"market": "ou", "company_id": "1", "company_name": "澳门"}]
        out = poll_nowgoal._fetch_corner_odds_for_companies(None, "999", main_records)
        assert out == []

    def test_waf_blocked_propagates_not_swallowed(self, monkeypatch):
        """WAF 拦截是全局性的,必须原样往上抛给调用方统一处理/退避,不能当成
        单公司失败吞掉(否则整轮请求都在被拦,却看起来只是"没有角球盘")。"""
        from backend.cli import poll_nowgoal

        def fake_fetch(*_a, **_k):
            raise poll_nowgoal.nowgoal.WAFBlockedError("命中 WAF")

        monkeypatch.setattr(poll_nowgoal.nowgoal, "fetch_corner_odds", fake_fetch)
        main_records = [{"market": "1x2", "company_id": "8", "company_name": "Bet365"}]
        with pytest.raises(poll_nowgoal.nowgoal.WAFBlockedError):
            poll_nowgoal._fetch_corner_odds_for_companies(None, "999", main_records)


# ── admin xref 审核端点 ──────────────────────────────────────────────────


def _login_user(client, ip):
    wechat_scan_login(client, ip=ip)
    assert client.get("/api/v1/me").json()["authenticated"]


def _login_admin(app, ip):
    from backend.cli.create_admin import create_admin

    conn = connect_rw("platform")
    try:
        create_admin(conn, "boss", "admin-pass-123", reset=True)
    finally:
        conn.close()
    admin = TestClient(app)
    r = admin.post("/api/v1/auth/password/login",
                   json={"username": "boss", "password": "admin-pass-123"},
                   headers={"x-real-ip": ip})
    assert r.status_code == 200
    return admin


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("allwin_csrf"), **ORIGIN}


class TestAdminXrefEndpoints:
    def _seed_xrefs(self, data_dir):
        conn = connect_rw("odds")
        try:
            _insert_xref(conn, "800001", 9001, status="needs_review")
            _insert_xref(conn, "800002", 9002, status="auto_ok")
            _insert_xref(conn, "800003", 9003, status="confirmed")
        finally:
            conn.close()

    def test_list_pending_includes_needs_review(self, app, data_dir, fresh_ip):
        self._seed_xrefs(data_dir)
        admin = _login_admin(app, fresh_ip)
        r = admin.get("/api/v1/admin/xref")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"
        body = r.json()
        statuses = {x["provider_match_id"]: x["review_status"] for x in body["xrefs"]}
        assert statuses == {"800001": "needs_review", "800002": "auto_ok"}
        assert body["counts"]["needs_review"] == 1
        # 精确过滤
        only = admin.get("/api/v1/admin/xref?status=confirmed").json()["xrefs"]
        assert [x["provider_match_id"] for x in only] == ["800003"]

    def test_confirm_and_reject_with_audit(self, app, data_dir, fresh_ip):
        self._seed_xrefs(data_dir)
        admin = _login_admin(app, fresh_ip)
        pending = admin.get("/api/v1/admin/xref").json()["xrefs"]
        target = next(x for x in pending if x["provider_match_id"] == "800001")

        r = admin.post(f"/api/v1/admin/xref/{target['id']}/confirm", headers=_csrf(admin))
        assert r.status_code == 200
        conn = connect_rw("odds")
        try:
            row = conn.execute("SELECT * FROM dim_match_xref WHERE id=?", (target["id"],)).fetchone()
        finally:
            conn.close()
        assert row["review_status"] == "confirmed"
        assert row["verified"] == 1 and row["method"] == "manual"

        other = next(x for x in pending if x["provider_match_id"] == "800002")
        r2 = admin.post(f"/api/v1/admin/xref/{other['id']}/reject", headers=_csrf(admin))
        assert r2.status_code == 200

        logs = admin.get("/api/v1/admin/audit-logs").json()["logs"]
        actions = [l["action"] for l in logs]
        assert "xref.confirm" in actions and "xref.reject" in actions
        confirm_log = next(l for l in logs if l["action"] == "xref.confirm")
        assert json.loads(confirm_log["detail_json"])["provider_match_id"] == "800001"

        r404 = admin.post("/api/v1/admin/xref/99999/confirm", headers=_csrf(admin))
        assert r404.status_code == 404

    def test_permissions(self, app, client, data_dir, fresh_ip):
        self._seed_xrefs(data_dir)
        _login_user(client, fresh_ip)   # 普通用户
        assert client.get("/api/v1/admin/xref").status_code == 403
        assert client.post("/api/v1/admin/xref/1/confirm",
                           headers=_csrf(client)).status_code == 403
        anon = TestClient(app)
        assert anon.get("/api/v1/admin/xref").status_code == 401
        # 管理员但无 CSRF/Origin → 403
        admin = _login_admin(app, fresh_ip)
        assert admin.post("/api/v1/admin/xref/1/confirm").status_code == 403
