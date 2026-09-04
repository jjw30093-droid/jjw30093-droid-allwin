"""首页战绩 banner 择优选择器(backend/queries/reco_highlight.py)。

本模块是**故意的择优展示**(经站长明确决定,见模块头注)。测试的职责不是
"证明它公平",而是证明它**确定性、可复现、且每个数字都真实**:
- 连中定义精确(排序、push/half_win/voided 各自怎么算);
- 连中是"当前"连中而不是历史最佳(这条是防事实性谎言的硬约束);
- 排序用 published_at,重结算改写 settled_at 不影响结果;
- 破平全序,输入顺序打乱不改变输出。
"""

from datetime import datetime

from backend.queries.reco_highlight import (
    HIT_RATE_THRESHOLD,
    Candidate,
    SlipFact,
    build_candidates,
    current_streak,
    ordered_slips,
    select_for_board,
    select_highlights,
)

NOW = datetime(2026, 9, 4, 12, 0, 0)


def slip(
    sid, *, board="daily_pick", result="win", combo="single", date="2026-09-01",
    published=None, status="settled", market="ah", league=47, ret=1.9,
):
    return SlipFact(
        id=sid, board=board, combo_type=combo, slip_date=date,
        published_at=published if published is not None else f"2026-09-01T00:00:{sid[-2:]}Z",
        status=status, result=result, return_units=ret,
        market=market, league_id=league,
    )


# ── 排序 ────────────────────────────────────────────────────────

class TestOrdering:
    def test_orders_by_published_at_then_id(self):
        a = slip("s01", published="2026-09-01T10:00:00Z")
        b = slip("s02", published="2026-09-01T09:00:00Z")
        assert [s.id for s in ordered_slips([a, b], "daily_pick")] == ["s02", "s01"]

    def test_resettlement_does_not_change_order(self):
        """核心:settled_at 会被重结算覆盖,published_at 不会。SlipFact 里
        根本没有 settled_at 字段——排序在结构上就不可能依赖它。"""
        assert not hasattr(slip("s01"), "settled_at")

    def test_null_published_at_is_defensive_not_fatal(self):
        a = slip("s01", published=None)
        b = slip("s02", published="2026-09-01T09:00:00Z")
        out = ordered_slips([a, b], "daily_pick")
        assert [s.id for s in out] == ["s01", "s02"]   # None → "" 排最前,确定即可

    def test_input_order_does_not_matter(self):
        a = slip("s01", published="2026-09-01T10:00:00Z")
        b = slip("s02", published="2026-09-01T09:00:00Z")
        assert ordered_slips([a, b], "daily_pick") == ordered_slips([b, a], "daily_pick")


# ── 连中 ────────────────────────────────────────────────────────

class TestStreak:
    def _seq(self, results):
        """按时间升序造样本:results[0] 最早。"""
        return [
            slip(f"s{i:02d}", result=r, published=f"2026-09-01T{i:02d}:00:00Z")
            for i, r in enumerate(results, start=1)
        ]

    def test_production_sequence_gives_five(self):
        """生产真实序列(published_at 倒序):win win win win win push lose。
        连中 = 5,且 push 落在第 6 位——这个 5 在"push 跳过"与"push 断连"
        两种规则下都成立。正是站长举例的「近5单全中」。"""
        seq = self._seq(["lose", "push", "win", "win", "win", "win", "win"])
        st = current_streak(seq, "daily_pick")
        assert st is not None and st.length == 5

    def test_lose_breaks(self):
        st = current_streak(self._seq(["win", "win", "lose"]), "daily_pick")
        assert st is None

    def test_half_loss_breaks(self):
        st = current_streak(self._seq(["win", "win", "half_loss"]), "daily_pick")
        assert st is None

    def test_half_win_breaks(self):
        """「全中」不能由半赢撑起来。"""
        st = current_streak(self._seq(["win", "win", "half_win"]), "daily_pick")
        assert st is None

    def test_push_is_skipped_not_counted_not_breaking(self):
        # 走水夹在两个命中之间 → 连中延续,且如实披露"其间 1 单走水"
        st = current_streak(self._seq(["win", "push", "win", "win"]), "daily_pick")
        assert st is not None
        assert st.length == 3 and st.skipped_push == 1

    def test_net_units_covers_exactly_the_streak_slips(self):
        """净回报的样本必须与 length 逐单一致——横条上「近 N 单全中」和
        「回报 +X%」是同一行里的两个数字,取自不同批次就是在骗人。

        时序(升序):lose(0) win(3.0) push(1.0) win(1.5) win(2.0)
        连中从最新往回数 = 2(win 1.5 / win 2.0),push 跳过后遇到 win(3.0)
        才延续……注意 push 不断连,所以连中其实是 3。
        """
        seq = [
            slip("s01", result="lose", ret=0.0, published="2026-09-01T01:00:00Z"),
            slip("s02", result="win", ret=3.0, published="2026-09-01T02:00:00Z"),
            slip("s03", result="push", ret=1.0, published="2026-09-01T03:00:00Z"),
            slip("s04", result="win", ret=1.5, published="2026-09-01T04:00:00Z"),
            slip("s05", result="win", ret=2.0, published="2026-09-01T05:00:00Z"),
        ]
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 3, "push 跳过不断连"
        # 3.0 + 1.5 + 2.0 = 6.5,减 3 单本金 → 3.5。
        # **被跳过的 push(1.0)不进分子**,连中之外的 lose(0.0)也不进。
        assert st.net_units == 3.5
        assert st.skipped_push == 1

    def test_net_units_excludes_slips_outside_the_streak(self):
        """连中之外更早的赢单,回报一分都不能算进来。"""
        seq = [
            slip("s01", result="win", ret=9.99, published="2026-09-01T01:00:00Z"),
            slip("s02", result="lose", ret=0.0, published="2026-09-01T02:00:00Z"),
            slip("s03", result="win", ret=2.0, published="2026-09-01T03:00:00Z"),
        ]
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 1
        assert st.net_units == 1.0, "s01 的 9.99 在 lose 之前,绝不能计入"

    def test_net_units_counts_parlay_inside_streak(self):
        """生产当前就是这个形态:5 连中里混着一张 2 串 1(返还 3.66)。
        连中本来就不分单关/串关,回报口径必须跟连中保持同一批样本。"""
        seq = [
            slip("s01", result="win", ret=2.0, published="2026-09-01T01:00:00Z"),
            slip("s02", result="win", combo="parlay", ret=3.66,
                 published="2026-09-01T02:00:00Z"),
        ]
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 2
        assert st.net_units == 3.66  # (2.0 + 3.66) - 2

    def test_net_units_skips_voided(self):
        """作废单跳过 → 不进分子也不进分母(它的 return_units 是结算时的
        残值,void_slip 不清空该列)。"""
        seq = [
            slip("s01", result="win", ret=2.0, published="2026-09-01T01:00:00Z"),
            slip("s02", result="win", ret=5.0, status="voided",
                 published="2026-09-01T02:00:00Z"),
            slip("s03", result="win", ret=1.5, published="2026-09-01T03:00:00Z"),
        ]
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 2 and st.skipped_void == 1
        assert st.net_units == 1.5, "作废单的 5.0 残值不得混进回报"

    def test_production_streak_net_units_matches_real_data(self):
        """生产真实数据回归:5 单 return_units = 3.66/1.425/1.95/1.93/2.0
        → 净 +5.965,回报率 5.965/5 = +119%。"""
        rets = [2.0, 1.93, 1.95, 1.425, 3.66]      # 升序(最早在前)
        seq = [
            slip(f"s{i:02d}", result="win", ret=r,
                 published=f"2026-09-01T{i:02d}:00:00Z")
            for i, r in enumerate(rets, start=1)
        ]
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 5
        assert st.net_units == 5.965
        assert round(st.net_units / st.length * 100) == 119

    def test_trailing_push_not_counted_as_within_streak(self):
        """生产真实形态:走水落在连中**之外**(更早),不是夹在中间。
        此时不能说"其间 1 单走水不计"——那是对事实的错误描述。"""
        # 时序:lose, push, win, win, win → 连中 3,走水在连中之前
        st = current_streak(self._seq(["lose", "push", "win", "win", "win"]), "daily_pick")
        assert st is not None
        assert st.length == 3
        assert st.skipped_push == 0, "连中之外的走水不得计入'其间'披露"

    def test_voided_within_streak_is_skipped_and_disclosed(self):
        """作废单夹在两个命中**之间** → 跳过且披露。"""
        seq = [
            slip("s01", result="win", published="2026-09-01T01:00:00Z"),
            slip("s02", result="win", status="voided", published="2026-09-01T02:00:00Z"),
            slip("s03", result="win", published="2026-09-01T03:00:00Z"),
        ]
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 2 and st.skipped_void == 1

    def test_trailing_voided_not_counted_as_within_streak(self):
        """作废单在连中**之外**(比连中更新)→ 不计入"其间"披露。"""
        seq = self._seq(["win", "win"])
        seq.append(slip("s09", status="voided", result="win",
                        published="2026-09-01T09:00:00Z"))
        st = current_streak(seq, "daily_pick")
        assert st is not None
        assert st.length == 2 and st.skipped_void == 0

    def test_current_streak_not_best_historical(self):
        """时序:lose,win×5,lose → 最新是 lose,连中必须是 0,
        不能去挑中间那段 5 连胜冒充"近期"。"""
        seq = self._seq(["lose", "win", "win", "win", "win", "win", "lose"])
        assert current_streak(seq, "daily_pick") is None

    def test_empty(self):
        assert current_streak([], "daily_pick") is None

    def test_below_min_streak_not_used_as_highlight(self):
        seq = self._seq(["lose", "win", "win"])          # 连中 2 < MIN_STREAK
        h = select_for_board(seq, "daily_pick", NOW)
        assert h.kind != "streak"

    def test_parlay_counts_toward_streak(self):
        seq = self._seq(["win", "win"])
        seq.append(slip("s09", combo="parlay", published="2026-09-01T09:00:00Z"))
        st = current_streak(seq, "daily_pick")
        assert st is not None and st.length == 3


# ── 命中率候选 ──────────────────────────────────────────────────

class TestRateCandidates:
    def test_only_singles_count_toward_hit_rate(self):
        """站长决策:串子是串子。串关不得混进命中率候选。"""
        rows = [
            slip("s01", result="win"),
            slip("s02", result="lose", combo="parlay"),
        ]
        cands = [c for c in build_candidates(rows, "daily_pick", NOW)
                 if c.kind == "rate" and c.segment_kind == "overall"]
        assert cands and all(c.decided == 1 and c.win == 1 for c in cands)

    def test_parlay_gets_return_candidate_not_hit_rate(self):
        rows = [slip("s01", combo="parlay", result="win", ret=3.66)]
        cands = build_candidates(rows, "daily_pick", NOW)
        parlay = [c for c in cands if c.kind == "parlay_return"]
        assert parlay and parlay[0].hit_rate is None
        assert parlay[0].net_units == 2.66

    def test_push_not_in_denominator(self):
        rows = [slip("s01", result="win"), slip("s02", result="push")]
        c = next(c for c in build_candidates(rows, "daily_pick", NOW)
                 if c.segment_kind == "overall" and c.window_value == 30)
        assert c.decided == 1 and c.push == 1 and c.hit_rate == 1.0

    def test_all_push_produces_no_candidate(self):
        """全走水:分母为 0,既不是 0% 也不是 100%,不生成候选。"""
        rows = [slip("s01", result="push"), slip("s02", result="push")]
        assert not [c for c in build_candidates(rows, "daily_pick", NOW) if c.kind == "rate"]

    def test_count_window_not_generated_when_sample_short(self):
        """用 6 单冒充"最近 10 单"是虚假标签。"""
        rows = [slip(f"s{i:02d}") for i in range(1, 7)]
        cands = build_candidates(rows, "daily_pick", NOW)
        assert not [c for c in cands if c.window_kind == "count" and c.window_value == 10]
        assert [c for c in cands if c.window_kind == "count" and c.window_value == 5]

    def test_league_without_zh_name_is_dropped(self):
        rows = [slip("s01", league=99999)]
        cands = build_candidates(rows, "daily_pick", NOW, known_league_ids={47: "英超"})
        assert not [c for c in cands if c.segment_kind in ("league", "league_market")]

    def test_league_none_still_counts_in_overall_and_market(self):
        rows = [slip("s01", league=None)]
        cands = build_candidates(rows, "daily_pick", NOW, known_league_ids={47: "英超"})
        kinds = {c.segment_kind for c in cands if c.kind == "rate"}
        assert "overall" in kinds and "market" in kinds
        assert "league" not in kinds

    def test_observed_dates_are_actual_not_nominal(self):
        rows = [slip("s01", date="2026-08-20"), slip("s02", date="2026-09-01")]
        c = next(c for c in build_candidates(rows, "daily_pick", NOW)
                 if c.segment_kind == "overall" and c.window_value == 30)
        assert c.observed_from == "2026-08-20" and c.observed_to == "2026-09-01"

    def test_now_is_injected_not_read_from_clock(self):
        # 5 单:刚好够生成最小的场次窗口(COUNT_WINDOWS 最小值 5)。
        rows = [slip(f"s{i:02d}", date="2026-09-01",
                     published=f"2026-09-01T{i:02d}:00:00Z") for i in range(1, 6)]
        far = datetime(2030, 1, 1)
        cands = build_candidates(rows, "daily_pick", far)
        # 天窗口依赖 now → 远期 now 下全部出窗
        assert not [c for c in cands if c.window_kind == "days"]
        # 场次窗口不依赖 now → 仍在。两者对比证明 now 确实是注入的而非读时钟。
        assert [c for c in cands if c.window_kind == "count"]


# ── 阈值与择优 ──────────────────────────────────────────────────

class TestSelection:
    def _rows(self, wins, loses):
        out = [slip(f"w{i:02d}", result="win", published=f"2026-09-01T{i:02d}:00:00Z")
               for i in range(1, wins + 1)]
        out += [slip(f"l{i:02d}", result="lose", published=f"2026-09-02T{i:02d}:00:00Z")
                for i in range(1, loses + 1)]
        return out

    def test_exactly_threshold_qualifies(self):
        h = select_for_board(self._rows(7, 3), "daily_pick", NOW)
        assert h.candidate.hit_rate == HIT_RATE_THRESHOLD
        assert h.kind == "rate_qualified"

    def test_just_below_threshold_is_best_effort(self):
        h = select_for_board(self._rows(69, 31), "daily_pick", NOW)
        assert h.kind == "rate_best_effort"

    def test_all_losses_still_returns_something(self):
        """站长要求不留空:全输也要挑最不难看的挂,不得返回空。"""
        h = select_for_board(self._rows(0, 5), "daily_pick", NOW)
        assert h.kind == "rate_best_effort"
        assert h.candidate is not None and h.candidate.hit_rate == 0.0

    def test_streak_wins_over_rate(self):
        h = select_for_board(self._rows(5, 0), "daily_pick", NOW)
        assert h.kind == "streak" and h.streak.length == 5

    def test_rate_ladder_activates_once_streak_broken(self):
        """连中被打断后,阶梯才生效——证明它不是死代码。"""
        rows = self._rows(5, 0) + [
            slip("z99", result="lose", published="2026-09-03T00:00:00Z")]
        h = select_for_board(rows, "daily_pick", NOW)
        assert h.kind in ("rate_qualified", "rate_best_effort")

    def test_deterministic_regardless_of_input_order(self):
        rows = self._rows(4, 2)
        a = select_for_board(rows, "daily_pick", NOW)
        b = select_for_board(list(reversed(rows)), "daily_pick", NOW)
        assert a.kind == b.kind
        assert a.candidate.key == b.candidate.key

    def test_tie_break_prefers_larger_sample(self):
        """两个 100% 候选:样本大的赢。"""
        rows = [slip(f"a{i:02d}", market="ou", published=f"2026-09-01T{i:02d}:00:00Z")
                for i in range(1, 6)]
        rows += [slip(f"b{i:02d}", market="ah", published=f"2026-09-02T{i:02d}:00:00Z")
                 for i in range(1, 4)]
        cands = [c for c in build_candidates(rows, "daily_pick", NOW)
                 if c.kind == "rate" and c.segment_kind == "market"
                 and c.window_kind == "days" and c.window_value == 30]
        best = max(cands, key=lambda c: (c.hit_rate, c.decided))
        assert best.market == "ou" and best.decided == 5


# ── 板块分离 ────────────────────────────────────────────────────

class TestBoards:
    def test_boards_computed_separately(self):
        rows = [
            slip("s01", board="daily_pick", result="win"),
            slip("s02", board="daily_public", result="lose"),
        ]
        out = {h.board: h for h in select_highlights(rows, NOW)}
        assert out["daily_pick"].candidate.hit_rate == 1.0
        assert out["daily_public"].candidate.hit_rate == 0.0

    def test_empty_board_returns_empty_kind_not_zero_percent(self):
        """公推生产现状:零已结算。不能产出"0 单 0%"这种行。"""
        rows = [slip("s01", board="daily_pick")]
        out = {h.board: h for h in select_highlights(rows, NOW)}
        assert out["daily_public"].kind == "empty"
        assert out["daily_public"].candidate is None

    def test_both_empty(self):
        assert all(h.kind == "empty" for h in select_highlights([], NOW))


# ── 端点(GET /api/v1/reco/highlight)────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402

from .coreseed import insert_match, seed_core_schema  # noqa: E402
from backend.db.connections import connect_rw  # noqa: E402
from .test_reco import (  # noqa: E402
    _admin_client, _create_slip, _csrf, _prov_leg, _publish,
)


def _settle_win(admin, sid):
    legs = admin.get("/api/v1/admin/reco/slips").json()["slips"]
    leg_id = next(s for s in legs if s["id"] == sid)["legs"][0]["id"]
    r = admin.post(f"/api/v1/admin/reco/slips/{sid}/settle",
                    headers=_csrf(admin), json={"leg_results": {leg_id: "win"}})
    assert r.status_code == 200, r.text


class TestHighlightEndpoint:
    def _seed_core(self, match_id, league_id=47):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, match_id, league_id=league_id, date="2026-09-01",
                     status="Finish", kickoff_at_utc="2026-09-01T12:00:00Z")
        conn.commit()
        conn.close()

    def test_anonymous_200_with_counts_not_just_rate(self, app, data_dir, fresh_ip):
        """响应必须带原始计数——只给 hit_rate 的话前端就没法做到
        "百分比与计数同现"。"""
        self._seed_core(940001)
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="战绩样本", board="daily_public",
                            legs=[_prov_leg("A vs B", "ah", "主胜", 1.9, 940001)])
        _publish(admin, sid)
        _settle_win(admin, sid)

        r = TestClient(app).get("/api/v1/reco/highlight")
        assert r.status_code == 200
        body = r.json()
        pub = next(b for b in body["boards"] if b["board"] == "daily_public")
        assert pub["rate"] is not None
        assert pub["rate"]["win_count"] == 1 and pub["rate"]["decided_count"] == 1
        assert pub["candidate_key"]          # 可复现

    def test_streak_dto_carries_net_units(self, app, data_dir, fresh_ip):
        """streak 分支必须下发 net_units——前端靠 net_units / length 派生
        回报率。**刻意不下发算好的百分比**:同 rate 把 hit_rate 与
        decided_count 塞在一起的用意,让"只渲染百分比不渲染样本量"很别扭。
        """
        for i, mid in enumerate((940010, 940011, 940012), start=1):
            self._seed_core(mid)
        admin = _admin_client(app, data_dir, fresh_ip)
        for i, mid in enumerate((940010, 940011, 940012), start=1):
            sid = _create_slip(admin, title=f"连中样本{i}", board="daily_public",
                                legs=[_prov_leg("A vs B", "ah", "主胜", 2.0, mid)])
            _publish(admin, sid)
            _settle_win(admin, sid)

        body = TestClient(app).get("/api/v1/reco/highlight").json()
        pub = next(b for b in body["boards"] if b["board"] == "daily_public")
        assert pub["kind"] == "streak"
        st = pub["streak"]
        assert st["length"] == 3
        # 每单赔率 2.0 → 返还 2.0,3 单净 = 6.0 - 3 = 3.0,回报率 100%
        assert st["net_units"] == 3.0
        assert round(st["net_units"] / st["length"] * 100) == 100
        # 百分比不由后端下发
        assert "roi" not in st and "return_rate" not in st

    def test_never_leaks_slip_content(self, app, data_dir, fresh_ip):
        """banner 只出聚合数字,绝不出单据内容(标题/选项/赔率/思路)。"""
        self._seed_core(940002)
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="绝密标题哨兵", board="daily_public",
                            legs=[_prov_leg("A vs B", "ah", "主胜哨兵", 1.9, 940002)])
        _publish(admin, sid)
        _settle_win(admin, sid)

        text = TestClient(app).get("/api/v1/reco/highlight").text
        for sentinel in ("绝密标题哨兵", "主胜哨兵", "odds", "match_desc"):
            assert sentinel not in text, f"战绩 banner 不得下发 {sentinel}"

    def test_cache_control(self, app, data_dir, fresh_ip):
        anon = TestClient(app)
        assert anon.get("/api/v1/reco/highlight").headers["cache-control"] == (
            "public, s-maxage=300, stale-while-revalidate=60"
        )
        admin = _admin_client(app, data_dir, fresh_ip)
        assert admin.get("/api/v1/reco/highlight").headers["cache-control"] == (
            "private, no-store"
        )

    def test_does_not_change_record_face_numbers(self, app, data_dir, fresh_ip):
        """红线:择优只存在于 banner。记录面 /reco/track-record 与
        /reco/overview 的数字逐字段不变。"""
        self._seed_core(940003)
        admin = _admin_client(app, data_dir, fresh_ip)
        sid = _create_slip(admin, title="红线样本",
                            legs=[_prov_leg("A vs B", "ah", "主胜", 1.9, 940003)])
        _publish(admin, sid)
        _settle_win(admin, sid)

        anon = TestClient(app)
        before = (anon.get("/api/v1/reco/track-record").json(),
                  anon.get("/api/v1/reco/overview").json())
        anon.get("/api/v1/reco/highlight")
        after = (anon.get("/api/v1/reco/track-record").json(),
                 anon.get("/api/v1/reco/overview").json())
        assert after == before

    def test_empty_db_returns_all_empty_not_zero_percent(self, app, data_dir, fresh_ip):
        r = TestClient(app).get("/api/v1/reco/highlight")
        assert r.status_code == 200
        assert all(b["kind"] == "empty" for b in r.json()["boards"])
        assert all(b["rate"] is None for b in r.json()["boards"])
