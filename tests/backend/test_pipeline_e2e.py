"""收口 P0-1:离线端到端数据链路测试(临时三库 + 固定 fixture,不碰真实 data/*.db)。

链路:core 精确 kickoff → NowGoal --due 轮询(日程发现→xref→hash-diff 快照×2 且字段变化)
→ FotMob 阵容/伤停快照×2 且变化 → silver_odds_moves / silver_event_moves
→ gold_move_cooccurrence → API 赔率时间轴 + 同期事件 → analysis_bundle。
重跑幂等;窗口节流(15/5 分钟)持久化于 poll_state。
真实 NowGoal/FotMob 端点 UNVERIFIED —— 此处验证的是同一条代码链路的离线走法。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.cli.build_odds_silver import run as run_odds_silver
from backend.cli.poll_fotmob_snapshots import run_snapshot_poll
from backend.cli.poll_nowgoal import run_due_poll
from backend.db.connections import connect_ro, connect_rw
from backend.db.util import normalize_utc_iso
from backend.silver.odds_moves import final_pre_match_snapshot
from backend.studio.bundle import build_analysis_bundle

from .authflow import wechat_scan_login

ORIGIN = {"Origin": "http://localhost:3000"}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc).replace(microsecond=0)
T0 = _iso(NOW)
T0_PLUS_5MIN = _iso(NOW + timedelta(minutes=5))
T1 = _iso(NOW + timedelta(hours=1))             # 1 小时 → 第二轮 due(见下)
T2 = _iso(NOW + timedelta(hours=1, minutes=5))  # T1+5min → watch 档(300s)再次 due
# NowGoal 三段节流(PIPELINE_REDESIGN_V2 P1)下:距开球 2.5h 落在 T-6h..T-0 档
# (间隔 3600s=1 小时)。T0 首轮 due;T0+5min(300s)节流;T0+1h(3600s)到期——
# 保持本用例"先节流再到期"的原意。League_ID=47(英超,BIG-5)驱动 FotMob 阵容
# watch-window-start=T-1.5h;T1=kickoff-1.5h 恰好跨进 5 分钟档,f1/f2 同样 due。
KICKOFF = NOW + timedelta(hours=2, minutes=30)
KICKOFF_ISO = _iso(KICKOFF)
# NowGoal 日程行内 kickoff 字段本身是 UTC(2026-07-21 真实 titan_id=2912218 交叉
# 验证,见 entity_resolution.py 模块 docstring),不做时区转换。
JS_TUPLE = f"{KICKOFF.year},{KICKOFF.month - 1},{KICKOFF.day},{KICKOFF.hour:02d},{KICKOFF.minute:02d},00"

MATCH_ID = 7001
TITAN = "888001"
HOME_ID, AWAY_ID = 111, 222


def _schedule_data() -> str:
    return (
        "var A=Array(2);\nvar matchcount=1;\n"
        f"A[1]=[{TITAN},36,{HOME_ID},{AWAY_ID},'Arsenal','Chelsea','{JS_TUPLE}',-1,0];"
    )


def _odds_payload(latest_home: str) -> dict:
    return {"companies": [{
        "cid": "8", "name": "Bet365",
        "euro": {
            "f": {"u": "2.10", "g": "3.40", "d": "3.50"},
            "l": {"u": latest_home, "g": "3.40", "d": "3.60"},
        },
    }]}


def _fixture_file(tmp_path, name: str, latest_home: str) -> str:
    p = tmp_path / name
    p.write_text(
        json.dumps({"schedule_data": _schedule_data(), "odds": {TITAN: _odds_payload(latest_home)}}),
        encoding="utf-8",
    )
    return str(p)


def _fm_payload(formation: str, home_sidelined: list[str], home_coach_id: int = 501) -> dict:
    """home_coach_id 默认恒定(501)——两轮常规调用(T0/T1)教练不变,只有 formation/
    伤停按参数变化。test_full_chain 的第三轮显式传入不同 id 单独隔离"只换教练"
    这一种变化(Fix 2 / C2 的链路镜像)。"""
    return {"content": {"lineup": {
        "homeTeam": {
            "id": HOME_ID, "formation": formation,
            "coach": {"id": home_coach_id, "name": f"Coach {home_coach_id}"},
            "starters": [{"id": 1, "name": "A"}], "subs": [],
            "unavailable": [
                {"id": 90 + i, "name": n, "unavailability": {"type": "injury"}}
                for i, n in enumerate(home_sidelined)
            ],
        },
        "awayTeam": {
            "id": AWAY_ID, "formation": "4-4-2",
            "coach": {"id": 601, "name": "Away Coach"},
            "starters": [{"id": 2, "name": "B"}], "subs": [], "unavailable": [],
        },
    }}}


@pytest.fixture
def pipeline_core(data_dir):
    """core migration 已建 dim_match 全量骨架;插入一场精确 kickoff 的未开赛比赛。"""
    conn = connect_rw("core")
    try:
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID,"
            " Away_Team_ID, Home_Team_Name, Away_Team_Name, status, Match_Round, kickoff_at_utc,"
            " kickoff_precision, kickoff_source)"
            " VALUES (?, '2026/2027', 47, ?, ?, ?, 'Arsenal', 'Chelsea', 'NotStarted', '1', ?,"
            " 'exact', 'fotmob:fixtures')",
            (MATCH_ID, KICKOFF_ISO[:10], HOME_ID, AWAY_ID, KICKOFF_ISO),
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS dim_team_i18n"
            " (Team_ID INTEGER PRIMARY KEY, name_en TEXT, name_zh TEXT, source TEXT, updated_at TEXT)"
        )
        conn.execute("INSERT INTO dim_team_i18n VALUES (?, 'Arsenal', '阿森纳', 't', '')", (HOME_ID,))
        conn.execute("INSERT INTO dim_team_i18n VALUES (?, 'Chelsea', '切尔西', 't', '')", (AWAY_ID,))
        conn.commit()
        yield conn
    finally:
        conn.close()


class TestNormalizeUtcIso:
    def test_full_iso_passthrough(self):
        assert normalize_utc_iso("2026-08-21T14:00:00Z") == "2026-08-21T14:00:00Z"

    def test_timezone_converted_to_utc(self):
        assert normalize_utc_iso("2026-08-21T22:00:00+08:00") == "2026-08-21T14:00:00Z"

    def test_date_only_is_none(self):
        assert normalize_utc_iso("2026-08-21") is None      # 不补 00:00 伪装精确

    def test_garbage_is_none(self):
        assert normalize_utc_iso("Tue, May 19, 2026") is None
        assert normalize_utc_iso(None) is None


class TestUpcomingPreciseMatchesAndMarketPhase:
    """8/9:date_only 午夜占位既不得进入采集候选,其 market_phase 也必须 unknown——
    不能只靠字符串形状(是否含 'T'、是否等于午夜)判断,必须走统一验证器。"""

    def _insert_match(self, conn, match_id, status, kickoff_at_utc, precision, source):
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID,"
            " Away_Team_ID, Home_Team_Name, Away_Team_Name, status, kickoff_at_utc,"
            " kickoff_precision, kickoff_source)"
            " VALUES (?, '2026/2027', 47, ?, 111, 222, 'Arsenal', 'Chelsea', ?, ?, ?, ?)",
            (match_id, (kickoff_at_utc or T0)[:10], status, kickoff_at_utc, precision, source),
        )

    def test_date_only_midnight_placeholder_excluded_from_upcoming(self, data_dir):
        from backend.ingest.poll_windows import upcoming_precise_matches

        conn = connect_rw("core")
        now = _iso(NOW)
        exact_ko = _iso(NOW + timedelta(hours=24))
        # date_only 午夜占位:kickoff_at_utc 字符串形似精确时间(当天 00:00Z),
        # 但 precision='date_only'——绝不能因为字符串形状像 ISO 时间就被采信。
        midnight_ko = _iso((NOW + timedelta(hours=48)).replace(hour=0, minute=0, second=0))
        self._insert_match(conn, 7201, "NotStarted", exact_ko, "exact", "fotmob:fixtures")
        self._insert_match(conn, 7202, "NotStarted", midnight_ko, "date_only", None)
        conn.commit()
        rows = upcoming_precise_matches(conn, now)
        ids = {r["Match_ID"] for r in rows}
        assert 7201 in ids
        assert 7202 not in ids
        conn.close()

    def test_exact_precision_but_naive_datetime_excluded_defense_in_depth(self, data_dir):
        """SQL 粗筛只能按 precision/source 过滤;即使某行 precision='exact' 且
        source 非空但 kickoff_at_utc 缺显式时区(naive),Python 层
        normalize_exact_kickoff 兜底仍必须排除——防御纵深不能只信 SQL 粗筛。"""
        from backend.ingest.poll_windows import upcoming_precise_matches

        conn = connect_rw("core")
        now = _iso(NOW)
        naive_ko = (NOW + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")  # 无 Z/offset
        self._insert_match(conn, 7203, "NotStarted", naive_ko, "exact", "fotmob:fixtures")
        conn.commit()
        rows = upcoming_precise_matches(conn, now)
        assert 7203 not in {r["Match_ID"] for r in rows}
        conn.close()

    def test_market_phase_unknown_for_date_only_midnight_placeholder(self, data_dir):
        from backend.cli.poll_nowgoal import _market_phase

        conn = connect_rw("core")
        now = _iso(NOW)
        midnight_ko = _iso((NOW + timedelta(hours=48)).replace(hour=0, minute=0, second=0))
        self._insert_match(conn, 7301, "NotStarted", midnight_ko, "date_only", None)
        exact_ko = _iso(NOW + timedelta(hours=24))
        self._insert_match(conn, 7302, "NotStarted", exact_ko, "exact", "fotmob:fixtures")
        conn.commit()
        # date_only 占位:即使字符串形似"当天午夜"的精确时间,也必须 unknown,
        # 不得被误判为 pre_match(更不能因 now < 那个午夜字符串就"看起来像"赛前)。
        assert _market_phase(conn, 7301, now) == "unknown"
        # 真正精确且未开球 → pre_match
        assert _market_phase(conn, 7302, now) == "pre_match"
        conn.close()

    def test_market_phase_in_play_overrides_kickoff(self, data_dir):
        from backend.cli.poll_nowgoal import _market_phase

        conn = connect_rw("core")
        now = _iso(NOW)
        exact_ko = _iso(NOW + timedelta(hours=24))
        self._insert_match(conn, 7303, "InPlay", exact_ko, "exact", "fotmob:fixtures")
        conn.commit()
        assert _market_phase(conn, 7303, now) == "in_play"
        conn.close()

    def test_negative_offset_kickoff_not_excluded_by_coarse_sql(self, data_dir):
        """20. 带负时区偏移的合法 kickoff 不得被 SQL 粗筛(裸文本范围)错误排除。

        kickoff '2099-01-01T20:30:00-05:00' = UTC 2099-01-02T01:30:00Z,
        now = 2099-01-02T00:00:00Z → 距开球 1.5h,应在 72h 窗口内。
        裸文本比较会因 '2099-01-01T20:30...' < now '2099-01-02T00:00...' 而把它误当成
        "已过 now",从而错误排除;julianday 比较才正确。
        """
        from backend.ingest.poll_windows import upcoming_precise_matches

        conn = connect_rw("core")
        now = "2099-01-02T00:00:00Z"
        self._insert_match(conn, 7401, "NotStarted", "2099-01-01T20:30:00-05:00",
                           "exact", "fotmob:fixtures")
        # 对照:一个 +08:00 的合法 kickoff(= 2099-01-02T10:00Z,距 now 10h,窗口内)
        self._insert_match(conn, 7402, "NotStarted", "2099-01-02T18:00:00+08:00",
                           "exact", "fotmob:fixtures")
        conn.commit()
        ids = {r["Match_ID"] for r in upcoming_precise_matches(conn, now)}
        assert 7401 in ids and 7402 in ids
        conn.close()


class TestT7DiscoveryWindow:
    """T+7 首次发现即采(2026-08-12 真实回归修的 gap):run_due_poll 的候选池
    必须覆盖到 DISCOVERY_WINDOW_HOURS(168h),不能停在 poll_decision 内部的
    72h 节流窗口——否则开球在 72h 开外的新赛季首轮(如西甲首轮 kickoff 在
    T+3~T+10 天)永远进不了候选列表,first_discovery 判定形同虚设。"""

    FAR_MATCH_ID = 7501
    FAR_TITAN = "888501"
    FAR_HOME_ID, FAR_AWAY_ID = 311, 322

    def test_match_beyond_72h_but_within_168h_is_discovered_and_polled_once(
        self, data_dir, tmp_path
    ):
        far_kickoff = NOW + timedelta(hours=120)  # 72h 外、168h 内
        js_tuple = (
            f"{far_kickoff.year},{far_kickoff.month - 1},{far_kickoff.day},"
            f"{far_kickoff.hour:02d},{far_kickoff.minute:02d},00"
        )
        schedule_data = (
            "var A=Array(2);\nvar matchcount=1;\n"
            f"A[1]=[{self.FAR_TITAN},87,{self.FAR_HOME_ID},{self.FAR_AWAY_ID},"
            f"'Alaves','Getafe','{js_tuple}',-1,0];"
        )
        fixture_path = tmp_path / "far.json"
        fixture_path.write_text(
            json.dumps({
                "schedule_data": schedule_data,
                "odds": {self.FAR_TITAN: _odds_payload("2.00")},
            }),
            encoding="utf-8",
        )

        conn = connect_rw("core")
        try:
            conn.execute(
                "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID,"
                " Away_Team_ID, Home_Team_Name, Away_Team_Name, status, Match_Round,"
                " kickoff_at_utc, kickoff_precision, kickoff_source)"
                " VALUES (?, '2026/2027', 87, ?, ?, ?, 'Alaves', 'Getafe', 'NotStarted', '1', ?,"
                " 'exact', 'fotmob:fixtures')",
                (self.FAR_MATCH_ID, _iso(far_kickoff)[:10], self.FAR_HOME_ID, self.FAR_AWAY_ID,
                 _iso(far_kickoff)),
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS dim_team_i18n"
                " (Team_ID INTEGER PRIMARY KEY, name_en TEXT, name_zh TEXT, source TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO dim_team_i18n VALUES (?, 'Alaves', '阿拉维斯', 't', '')",
                (self.FAR_HOME_ID,),
            )
            conn.execute(
                "INSERT INTO dim_team_i18n VALUES (?, 'Getafe', '赫塔菲', 't', '')",
                (self.FAR_AWAY_ID,),
            )
            conn.commit()
        finally:
            conn.close()

        s1 = run_due_poll(now_iso=T0, offline_fixture=str(fixture_path))
        # 候选池必须真的包含这场 120h 外的比赛(旧实现在这里就是 0)
        assert s1["window_candidates"] == 1
        assert s1["resolved_auto_ok"] == 1
        assert s1["due_matches"] == 1
        assert s1["odds_polled"] == 1
        assert s1["snapshots_inserted"] >= 1

        # 同一时刻重放:first_discovery 只触发一次,已经 mark_polled 过了
        s2 = run_due_poll(now_iso=T0_PLUS_5MIN, offline_fixture=str(fixture_path))
        assert s2["due_matches"] == 0
        assert s2["not_due_skipped"] == 0  # 72h 外仍算"窗口外",不计入节流计数


class TestOfflinePipelineE2E:
    def test_full_chain(self, pipeline_core, data_dir, app, tmp_path, fresh_ip):
        # ── 轮 1(T0):日程发现 → xref → 赔率快照 v1 ─────────────
        s1 = run_due_poll(now_iso=T0, offline_fixture=_fixture_file(tmp_path, "f1.json", "2.05"))
        assert s1["window_candidates"] == 1
        assert s1["resolved_auto_ok"] == 1
        assert s1["odds_polled"] == 1
        assert s1["snapshots_inserted"] >= 1

        # dim_team_xref 实际写入(auto_ok 时登记 provider 球队 id ↔ canonical)
        _conn = connect_ro("odds")
        team_xrefs = {r[0]: r[1] for r in _conn.execute(
            "SELECT provider_team_id, canonical_team_id FROM dim_team_xref")}
        _conn.close()
        assert team_xrefs == {str(HOME_ID): HOME_ID, str(AWAY_ID): AWAY_ID}

        # ── 轮 1.5(+5min):15 分钟节流生效,不重复采集 ─────────
        s15 = run_due_poll(
            now_iso=T0_PLUS_5MIN, offline_fixture=_fixture_file(tmp_path, "f1b.json", "2.05")
        )
        assert s15["due_matches"] == 0
        assert s15["not_due_skipped"] == 1

        # ── 轮 2(+20min):赔率字段变化 → 第二条快照 ─────────────
        s2 = run_due_poll(now_iso=T1, offline_fixture=_fixture_file(tmp_path, "f2.json", "1.95"))
        assert s2["due_matches"] == 1
        assert s2["snapshots_inserted"] == 1

        # ── FotMob 阵容/伤停两轮(阵型变化 + 伤停新增) ───────────
        f1 = run_snapshot_poll(
            now_iso=T0, offline_payloads={str(MATCH_ID): _fm_payload("4-3-3", [])}
        )
        assert f1["snapshots_inserted"] == 3        # lineup + 两队 sideline
        f2 = run_snapshot_poll(
            now_iso=T1, offline_payloads={str(MATCH_ID): _fm_payload("4-2-3-1", ["Saka"])}
        )
        # lineup 变化 + 主队伤停变化;客队伤停 hash 未变 → skip
        assert f2["snapshots_inserted"] == 2
        assert f2["snapshots_skipped"] == 1

        conn_odds = connect_ro("odds")

        # market_phase 精确判定:全部 pre_match(NotStarted + now < kickoff)
        phases = {r[0] for r in conn_odds.execute(
            "SELECT DISTINCT market_phase FROM bronze_ng_odds_snap")}
        assert phases == {"pre_match"}

        # 同轮共用 observed_at / poll_run_id(三条 FotMob 快照)
        runs = conn_odds.execute(
            """SELECT COUNT(DISTINCT poll_run_id), COUNT(DISTINCT observed_at)
               FROM (SELECT poll_run_id, observed_at FROM bronze_fm_lineup_snap WHERE observed_at=?
                     UNION ALL
                     SELECT poll_run_id, observed_at FROM bronze_fm_sideline_snap WHERE observed_at=?)""",
            (T0, T0),
        ).fetchone()
        assert tuple(runs) == (1, 1)

        # ── Silver / Gold ───────────────────────────────────────
        r1 = run_odds_silver()
        assert r1["odds_moves_inserted"] >= 1       # home 2.05→1.95
        assert r1["event_moves_inserted"] >= 2      # lineup_change + sideline_change
        assert r1["cooccurrence_inserted"] >= 1     # 变化同发生于 T1,delta=0 ≤ 900s

        # 重跑幂等
        r2 = run_odds_silver()
        assert r2 == {"odds_moves_inserted": 0, "event_moves_inserted": 0,
                      "cooccurrence_inserted": 0}

        # ── 第三轮 FotMob(T2):只改主教练,阵型/首发/伤停均与第二轮完全相同
        # (C2 test_coach_change_emits_a_described_lineup_change 的链路镜像:
        # 单元测试直接调 _lineup_change_summary,这里走完整 bronze→silver 链路)
        f3 = run_snapshot_poll(
            now_iso=T2,
            offline_payloads={str(MATCH_ID): _fm_payload("4-2-3-1", ["Saka"], home_coach_id=502)},
        )
        assert f3["snapshots_inserted"] == 1     # 只有 lineup 变了(教练);两队伤停 hash 不变
        assert f3["snapshots_skipped"] == 2
        r3 = run_odds_silver()
        assert r3["event_moves_inserted"] == 1   # 只有一条新 lineup_change(教练变化)
        lineup_move_3 = conn_odds.execute(
            "SELECT detail_json FROM silver_event_moves"
            " WHERE event_type='lineup_change' AND fotmob_match_id=?"
            " ORDER BY id DESC LIMIT 1",
            (MATCH_ID,),
        ).fetchone()
        detail3 = json.loads(lineup_move_3["detail_json"])
        assert detail3 == {"home": {"coach": {"prev": "Coach 501", "new": "Coach 502"}}}

        # FINAL:kickoff 前最后一条 pre_match 快照 = v2(需完整 provenance 三元组)
        fin = final_pre_match_snapshot(
            conn_odds, TITAN, "1x2", "8", KICKOFF_ISO, "exact", "fotmob:fixtures"
        )
        assert fin is not None
        assert json.loads(fin["payload_json"])["latest"]["home"] == 1.95
        # 无精确 kickoff(纯日期)→ 不声称收盘
        assert final_pre_match_snapshot(
            conn_odds, TITAN, "1x2", "8", KICKOFF_ISO[:10], "exact", "fotmob:fixtures"
        ) is None
        # precision 非 exact(即便 kickoff_at_utc 字符串本身合法)→ 同样不声称收盘
        assert final_pre_match_snapshot(
            conn_odds, TITAN, "1x2", "8", KICKOFF_ISO, "date_only", "fotmob:fixtures"
        ) is None
        # 缺来源 → 同样不声称收盘
        assert final_pre_match_snapshot(
            conn_odds, TITAN, "1x2", "8", KICKOFF_ISO, "exact", None
        ) is None

        # ── API:赔率时间轴 + 同期事件(premium) ─────────────────
        client = TestClient(app)
        wechat_scan_login(client, ip=fresh_ip)
        me = client.get("/api/v1/me").json()
        assert me["authenticated"]
        # 三段可见性(CLAUDE.md §8):登录即 member 基线,odds:history_full 无需订阅

        odds_resp = client.get(f"/api/v1/matches/{MATCH_ID}/odds").json()
        assert odds_resp["available"] is True
        assert odds_resp["tier"] == "full"
        assert len(odds_resp["snapshots"]) >= 2     # v1 + v2 完整时间线

        cooc = client.get(f"/api/v1/matches/{MATCH_ID}/cooccurrence").json()
        assert cooc["count"] >= 1
        assert cooc["items"] and cooc["items"][0]["event_type"] in ("lineup_change", "sideline_change")

        # ── analysis_bundle(详情页与 Studio 共用 builder) ───────
        conn_core_ro = connect_ro("core")
        conn_plat_ro = connect_ro("platform")
        try:
            bundle = build_analysis_bundle(conn_core_ro, conn_plat_ro, conn_odds, MATCH_ID)
        finally:
            conn_core_ro.close()
            conn_plat_ro.close()
            conn_odds.close()
        assert bundle is not None
        assert bundle["odds_timeline"], "bundle 应包含赔率时间轴"
        assert bundle["cooccurring_events"], "bundle 应包含同期事件"
        # 有精确 kickoff → 不再出现开球精度不确定性提示
        assert not any(u["kind"] == "kickoff_precision" for u in bundle["uncertainty"])
