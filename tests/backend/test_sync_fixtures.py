"""T+7 赛程同步测试(数据管道重建 Phase 2,全离线)。

覆盖:赛季发现、身份门禁、off_season 诚实空、G-A 反退化拒写、
G-B/G-C 已完赛/比分不被覆盖、INSERT OR REPLACE 清列缺陷已修(裁判/天气/比分保留)、
ledger 逐联赛落行、幂等。
"""

import pytest

from backend.cli import sync_fixtures_window as sfw
from backend.db.connections import connect_rw
from .coreseed import seed_core_schema


def _payload(league_id, season, matches):
    return {
        "details": {"id": league_id, "selectedSeason": season},
        "fixtures": {"allMatches": matches},
    }


def _match(mid, home_id, away_id, kickoff, status="notstarted", rnd="1"):
    st = {"utcTime": kickoff}
    if status == "finished":
        st["finished"] = True
    elif status == "started":
        st["started"] = True
    return {
        "id": mid,
        "home": {"id": home_id, "name": f"Home{home_id}"},
        "away": {"id": away_id, "name": f"Away{away_id}"},
        "status": st,
        "round": rnd,
    }


NOW = "2026-08-10T00:00:00Z"


def _future(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime(2026, 8, 10, tzinfo=timezone.utc)
            + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def core(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    conn.commit()
    conn.close()
    from backend.db import migrate
    migrate.apply_all("odds", quiet=True)


def _run(league_id, payload, dry_run=False):
    return sfw.run_sync(due_only=False, only_league=league_id, dry_run=dry_run,
                        now_iso=NOW, offline_payload=payload)


class TestSeasonDiscovery:
    def test_discovers_season_and_writes(self, core):
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])
        s = _run(48, pl)
        r = s["leagues"][0]
        assert r["verdict"] == "written"
        assert r["season"] == "2026/2027"
        assert r["written"] == 1
        conn = connect_rw("core")
        row = conn.execute("SELECT Season, status, Match_Round FROM dim_match WHERE Match_ID=1").fetchone()
        conn.close()
        assert row["Season"] == "2026/2027" and row["status"] == "NotStarted"

    def test_identity_mismatch_refused(self, core):
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])
        pl["details"]["id"] = 999
        s = _run(48, pl)
        assert s["leagues"][0]["verdict"] == "refused_identity"
        conn = connect_rw("core")
        n = conn.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0]
        conn.close()
        assert n == 0

    def test_offseason_empty_is_honest(self, core):
        pl = _payload(42, "2025/2026", [])
        s = _run(42, pl)
        assert s["leagues"][0]["verdict"] == "off_season"


class TestAntiRegression:
    def _seed_ledger(self, league_id, season, fetched):
        from backend.db.util import utc_now_iso
        conn = connect_rw("odds")
        conn.execute(
            "INSERT INTO fixture_sync_ledger (run_at,league_id,season,fetched_rows,"
            "horizon7_rows,written_rows,verdict) VALUES (?,?,?,?,?,?,'written')",
            (utc_now_iso(), league_id, season, fetched, fetched, fetched))
        conn.commit()
        conn.close()

    def test_ga_regression_refuses_write(self, core):
        self._seed_ledger(48, "2026/2027", 20)   # 上次 20 场
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])  # 本次仅 1 场
        s = _run(48, pl)
        assert s["leagues"][0]["verdict"] == "refused_regression"
        conn = connect_rw("core")
        n = conn.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0]
        conn.close()
        assert n == 0   # 保留旧数据(此处旧数据为空,但关键是没写入骤降数据)

    def test_no_baseline_writes_normally(self, core):
        pl = _payload(48, "2026/2027", [_match(i, 100 + i, 200 + i, _future(48)) for i in range(1, 4)])
        s = _run(48, pl)
        assert s["leagues"][0]["verdict"] == "written"


class TestClobberGuard:
    def test_referee_weather_score_preserved(self, core):
        """核心回归:赛程同步不得把已有的裁判/天气/比分清成 NULL(事故 #2 同型)。"""
        conn = connect_rw("core")
        # 先有一场带裁判/天气的未开赛比赛(模拟 poll_fotmob_snapshots 写过)
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,status,Referee,Temperature,Wind_Speed,"
            "kickoff_at_utc,kickoff_precision,kickoff_source) "
            "VALUES (1,'2026/2027',48,'2026-08-12',100,200,'H','A','NotStarted',"
            "'张裁判','22C','5kmh','2026-08-12T18:00:00Z','exact','fotmob:match_details')")
        conn.commit()
        conn.close()
        # 赛程同步再跑同一场(改期到不同时间)
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])
        _run(48, pl)
        conn = connect_rw("core")
        row = conn.execute(
            "SELECT Referee,Temperature,Wind_Speed,kickoff_at_utc FROM dim_match WHERE Match_ID=1").fetchone()
        conn.close()
        assert row["Referee"] == "张裁判", "裁判被清空 = INSERT OR REPLACE 清列缺陷回归"
        assert row["Temperature"] == "22C"
        assert row["Wind_Speed"] == "5kmh"
        assert row["kickoff_at_utc"] == _future(48)   # 开球时间正常更新(赛程拥有列)

    def test_finished_row_not_downgraded(self, core):
        conn = connect_rw("core")
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,home_score,away_score,status) "
            "VALUES (1,'2026/2027',48,100,200,'H','A',2,1,'Finish')")
        conn.commit()
        conn.close()
        # 来源错误地把已完赛比赛又当未开赛发回
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48), status="notstarted")])
        s = _run(48, pl)
        # 2026-09-07:从"整批拒写"改成"逐行剔除"。**守的核心一字未变**——
        # 已完赛行绝不被未完赛行覆盖;变的只是其余行不再被这一行连累。
        assert s["leagues"][0]["verdict"] == "written_with_conflicts"
        assert "1" in s["leagues"][0]["detail"], "被剔除的 Match_ID 必须记进 detail,不得静默丢弃"
        conn = connect_rw("core")
        row = conn.execute("SELECT status,home_score FROM dim_match WHERE Match_ID=1").fetchone()
        conn.close()
        assert row["status"] == "Finish" and row["home_score"] == 2   # 未被覆盖

    def test_conflict_row_skipped_but_other_rows_still_written(self, core):
        """荷甲(57)事故的直接回归:一条脏行不得冻结整个联赛的赛程同步。

        2026-09-06/07 生产实况:FotMob 对 match 5781733 返回自相矛盾的记录
        (既带比分 1-3、又标 notStarted/finished:false,开球还从 09-05 16:45Z
        改挂到 09-08 12:00Z)。旧的整批拒写让荷甲连续 4 次 written_rows=0、
        停摆 24 小时,265 行一行没进库,而且脏数据不会自愈=无限期冻结。
        """
        conn = connect_rw("core")
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,home_score,away_score,status) "
            "VALUES (1,'2026/2027',48,100,200,'H','A',2,1,'Finish')")
        conn.commit()
        conn.close()
        # 1 号是脏行(已完赛却被当未开赛发回),2/3 号是正常的未来赛程
        pl = _payload(48, "2026/2027", [
            _match(1, 100, 200, _future(48), status="notstarted"),
            _match(2, 101, 201, _future(48)),
            _match(3, 102, 202, _future(48)),
        ])
        s = _run(48, pl)
        lg = s["leagues"][0]
        assert lg["verdict"] == "written_with_conflicts"
        assert lg["written"] == 2, "另外两行必须照常写入,不得被脏行连累"

        conn = connect_rw("core")
        kept = conn.execute(
            "SELECT status,home_score FROM dim_match WHERE Match_ID=1").fetchone()
        others = conn.execute(
            "SELECT COUNT(*) FROM dim_match WHERE Match_ID IN (2,3)").fetchone()[0]
        conn.close()
        assert kept["status"] == "Finish" and kept["home_score"] == 2, "脏行不得覆盖已完赛数据"
        assert others == 2, "正常行必须落库"


class TestPostponedFinishedMatchKickoffOnly:
    """已完赛行被来源改期时,只放行开球时刻(2026-09-07,站长选定方案 B)。

    真实案例:荷甲 FC Utrecht vs Go Ahead Eagles(5781733)在**第 65 分钟被
    中断**、比分 1-3,FotMob 把它改期到 09-08 12:00Z 并把 started/finished
    置回 false。旧行为下守卫拒掉整行,而**被拒的正是唯一携带新开球时刻的行**,
    新时间永远进不了库,页面一直显示旧的 09-05 已完赛。

    方案 B 的取舍:不去猜"来源在破坏数据"还是"在更正数据",只放行**时间**
    这一个维度——时间写错可自愈(下次同步再改),status/比分写错会污染战绩与
    统计(不可自愈)。
    """

    def test_kickoff_updated_but_status_and_score_untouched(self, core):
        conn = connect_rw("core")
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,home_score,away_score,status,kickoff_at_utc) "
            "VALUES (1,'2026/2027',48,'2026-08-10',100,200,'H','A',1,3,'Finish',"
            "'2026-08-10T16:45:00Z')")
        conn.commit()
        conn.close()

        new_kickoff = _future(48)
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, new_kickoff, status="notstarted")])
        s = _run(48, pl)
        lg = s["leagues"][0]
        assert lg["verdict"] == "written_with_conflicts"
        assert lg["kickoff_only_updated"] == 1

        conn = connect_rw("core")
        row = conn.execute(
            "SELECT status,home_score,away_score,kickoff_at_utc,Date"
            " FROM dim_match WHERE Match_ID=1").fetchone()
        conn.close()
        # 时间跟着来源走了
        assert row["kickoff_at_utc"] == new_kickoff, "开球时刻必须更新到新值"
        assert row["Date"] == new_kickoff[:10], "Date 必须与新开球日一致,不能内部自相矛盾"
        # 但 status 与比分一动不动 —— 这是方案 B 的全部意义
        assert row["status"] == "Finish", "status 绝不能被降级"
        assert row["home_score"] == 1 and row["away_score"] == 3, "比分绝不能被清空"

    def test_other_rows_unaffected(self, core):
        """改期行与正常行同批时,两条路径互不干扰。"""
        conn = connect_rw("core")
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,home_score,away_score,status) "
            "VALUES (1,'2026/2027',48,'2026-08-10',100,200,'H','A',1,3,'Finish')")
        conn.commit()
        conn.close()
        pl = _payload(48, "2026/2027", [
            _match(1, 100, 200, _future(48), status="notstarted"),
            _match(2, 101, 201, _future(48)),
        ])
        s = _run(48, pl)
        lg = s["leagues"][0]
        assert lg["written"] == 1 and lg["kickoff_only_updated"] == 1
        conn = connect_rw("core")
        assert conn.execute(
            "SELECT status FROM dim_match WHERE Match_ID=1").fetchone()["status"] == "Finish"
        assert conn.execute(
            "SELECT COUNT(*) FROM dim_match WHERE Match_ID=2").fetchone()[0] == 1
        conn.close()

    def test_season_label_change_skips_row_entirely(self, core):
        """跨赛季分界的改期:整行不动,绝不为了写进去而改 Season。

        dim_match 有触发器要求 Season 必须等于按 (League_ID, Date) 推导的赛季
        (§6.3 / core 迁移 0011)。硬写会么触发器拒绝、么把赛季标错——后者正是
        2026-08-25 那次 878 行赛季错标事故的成因。
        """
        conn = connect_rw("core")
        conn.execute(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,home_score,away_score,status,kickoff_at_utc) "
            "VALUES (1,'2025/2026',48,'2026-05-10',100,200,'H','A',1,3,'Finish',"
            "'2026-05-10T16:45:00Z')")
        conn.commit()
        conn.close()
        # 来源给的是 2026/2027 赛季的行(赛季标签与库里不同)
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48), status="notstarted")])
        s = _run(48, pl)
        assert s["leagues"][0]["kickoff_only_updated"] == 0, "赛季标签会变时必须整行不动"
        conn = connect_rw("core")
        row = conn.execute(
            "SELECT Season,status,kickoff_at_utc FROM dim_match WHERE Match_ID=1").fetchone()
        conn.close()
        assert row["Season"] == "2025/2026" and row["status"] == "Finish"
        assert row["kickoff_at_utc"] == "2026-05-10T16:45:00Z", "时间也不该被改"


class TestFetchFailedDoesNotThrottle:
    """真实运行中复现过的缺陷:fetch_failed(网络/解析失败,没拿到任何答案)不得
    mark_polled——否则一次偶发 SSL/超时会让该联赛静默拉黑 6 小时,且 6 小时内
    每次 tick 都因 not_due 直接跳过、CLI 判定"无 fetch_failed"报退出码 0,
    把真实网络失败伪装成成功。"""

    def test_fetch_failed_does_not_mark_polled_allows_immediate_retry(self, core, monkeypatch):
        import backend.fotmob_client as fm

        calls = {"n": 0}

        class BoomThenOk:
            def league_matches(self, league_id):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("模拟 FotMobTransportError SSL 失败")
                return _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])

        monkeypatch.setattr(fm, "FotMobClient", BoomThenOk)

        s1 = sfw.run_sync(due_only=True, only_league=48, dry_run=False, now_iso=NOW)
        assert s1["leagues"][0]["verdict"] == "fetch_failed"

        # 紧接着再来一次(模拟 runner 60s 后重试,或下一次 15 分钟 chain tick):
        # 若 fetch_failed 被误 mark_polled 成 6h 节流,这里会变成 not_due 而不是
        # 真的再尝试一次。
        s2 = sfw.run_sync(due_only=True, only_league=48, dry_run=False, now_iso=NOW)
        assert s2["leagues"][0]["verdict"] == "written"
        assert calls["n"] == 2, "fetch_failed 后必须允许立即重试,不得被节流吞掉"

    def test_written_verdict_still_throttles_next_due_check(self, core, monkeypatch):
        """回归钉:上面的修复不能连带破坏正常场景——真正拿到答案(written)后,
        6 小时节流必须照常生效,不能变成每次都重新请求源站。"""
        import backend.fotmob_client as fm

        class AlwaysOk:
            def league_matches(self, league_id):
                return _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])

        monkeypatch.setattr(fm, "FotMobClient", AlwaysOk)

        s1 = sfw.run_sync(due_only=True, only_league=48, dry_run=False, now_iso=NOW)
        assert s1["leagues"][0]["verdict"] == "written"

        s2 = sfw.run_sync(due_only=True, only_league=48, dry_run=False, now_iso=NOW)
        assert s2["leagues"][0]["verdict"] == "not_due"

    def test_off_season_also_throttles(self, core, monkeypatch):
        """off_season(合法空结果,真正拿到了答案)同样应节流,不是只有 written 才算。"""
        import backend.fotmob_client as fm

        class EmptyAlways:
            def league_matches(self, league_id):
                return _payload(42, "2025/2026", [])

        monkeypatch.setattr(fm, "FotMobClient", EmptyAlways)

        s1 = sfw.run_sync(due_only=True, only_league=42, dry_run=False, now_iso=NOW)
        assert s1["leagues"][0]["verdict"] == "off_season"
        s2 = sfw.run_sync(due_only=True, only_league=42, dry_run=False, now_iso=NOW)
        assert s2["leagues"][0]["verdict"] == "not_due"


class TestLedgerAndIdempotency:
    def test_ledger_row_per_league(self, core):
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])
        _run(48, pl)
        conn = connect_rw("odds")
        rows = conn.execute("SELECT league_id,verdict,fetched_rows,written_rows FROM fixture_sync_ledger").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["league_id"] == 48 and rows[0]["verdict"] == "written"

    def test_rerun_idempotent(self, core):
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])
        _run(48, pl)
        _run(48, pl)
        conn = connect_rw("core")
        n = conn.execute("SELECT COUNT(*) FROM dim_match WHERE Match_ID=1").fetchone()[0]
        conn.close()
        assert n == 1   # 同一 Match_ID 不重复

    def test_dry_run_zero_side_effects(self, core):
        """dry-run 既不写 dim_match,也不写 ledger(零持久化副作用)。"""
        pl = _payload(48, "2026/2027", [_match(1, 100, 200, _future(48))])
        s = _run(48, pl, dry_run=True)
        assert s["leagues"][0]["written"] == 0
        conn = connect_rw("core")
        assert conn.execute("SELECT COUNT(*) FROM dim_match").fetchone()[0] == 0
        conn.close()
        conn = connect_rw("odds")
        assert conn.execute("SELECT COUNT(*) FROM fixture_sync_ledger").fetchone()[0] == 0
        conn.close()


class TestRealUclFixture:
    def test_ucl_playoff_kept_qualifying_absent(self, core):
        """用真实 2024/25 UCL 存档:playoff 场次保留,无 9 月前(资格赛)场次。"""
        import json
        from pathlib import Path
        raw = Path("runtime/research/pipeline-v2-probe/raw/fotmob_league_42.json")
        if not raw.exists():
            pytest.skip("Phase 0 UCL 探测产物不存在")
        data = json.loads(raw.read_text())
        # 探测产物只存了 fixture_sample(3 场)——够验证解析器不炸 + round 保留
        from backend.ingest.ingest_future_fixtures import rows_from_payload
        payload = {"details": data["details"],
                   "fixtures": {"allMatches": data.get("fixture_sample", [])}}
        rows = rows_from_payload(payload, 42, data["details"]["selectedSeason"])
        # 解析成功、round 字段透传(不因 'playoff' 之类字符串被丢弃)
        for r in rows:
            assert r["League_ID"] == 42
            assert r["Match_Round"] is not None
