"""dim_match_xref 5 处未过滤读取的回归(本轮任务 §5)。

背景:dim_match_xref 的 UNIQUE 是 (provider, fotmob_match_id),同一场比赛可以
同时有 nowgoal 与 kbisai 两条 xref。之前 5 处读取都是 `WHERE fotmob_match_id=?`
不带 provider 过滤 + `.fetchone()`——EXPLAIN QUERY PLAN 显示这是 SCAN(该表没有
可以按 fotmob_match_id 单独 seek 的索引),SQLite 返回的是 rowid 较小的那一行。

⚠️ 测试顺序很关键:必须先插 kbisai 行(拿到较小 rowid)再插 nowgoal 行。反过来插
(nowgoal 先插)在没打这个补丁的代码上也会通过——那是一次假绿,验证不出问题。
生产场景正是 kbisai-first:dim_match_xref 目前是空表,这一轮先写 kbisai 映射,
nowgoal 的映射后面才会陆续产生,rowid 一定更大。

bronze_ng_odds_snap 是 nowgoal 形状的表(无 provider 列),这里给 kbisai xref 一个
"看起来合理但其实是另一个 provider 命名空间"的 provider_match_id
(直接用 FotMob matchId 字符串,和 kbisai 真实 matchId 一样是纯数字,容易与
nowgoal 的 titan_id 撞在同一个数字空间里)。
"""

from backend.db.connections import connect_rw


KBISAI_PROVIDER_MATCH_ID = "4467576"     # 真实验证过的 kbisai matchId(挪超样例)
NOWGOAL_PROVIDER_MATCH_ID = "2912857"    # 真实验证过的 nowgoal titan_id(同一场比赛)
FOTMOB_MATCH_ID = 5104968


def _seed_colliding_xrefs(conn):
    """kbisai 先插(较小 rowid),nowgoal 后插(较大 rowid)——production 的真实顺序。"""
    conn.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, confidence, verified,
            method, review_status, created_at, updated_at)
           VALUES (?, 'kbisai', ?, 0.9, 1, 'auto', 'auto_ok', '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z')""",
        (FOTMOB_MATCH_ID, KBISAI_PROVIDER_MATCH_ID),
    )
    conn.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, confidence, verified,
            method, review_status, created_at, updated_at)
           VALUES (?, 'nowgoal', ?, 1.0, 1, 'manual', 'confirmed', '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z')""",
        (FOTMOB_MATCH_ID, NOWGOAL_PROVIDER_MATCH_ID),
    )
    conn.execute(
        """INSERT INTO bronze_ng_odds_snap
           (provider_match_id, market, company_id, company_name, market_phase,
            payload_json, payload_hash, observed_at, ingested_at)
           VALUES (?, '1x2', '3', 'Pinnacle', 'pre_match', '{"latest":{"home":1.5}}',
                   'hash-1', '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z')""",
        (NOWGOAL_PROVIDER_MATCH_ID,),
    )
    conn.commit()


def test_insertion_order_would_return_kbisai_row_without_the_provider_filter(data_dir):
    """先确认前提本身成立:不加 provider 过滤时,.fetchone() 真的拿到的是
    rowid 更小的 kbisai 行——如果这个前提本身不成立,下面几个"修复后"的断言
    就没有意义。"""
    conn = connect_rw("odds")
    try:
        _seed_colliding_xrefs(conn)
        row = conn.execute(
            "SELECT * FROM dim_match_xref WHERE fotmob_match_id=?", (FOTMOB_MATCH_ID,)
        ).fetchone()
        assert row["provider"] == "kbisai"
        assert row["provider_match_id"] == KBISAI_PROVIDER_MATCH_ID
    finally:
        conn.close()


def test_odds_endpoint_xref_query_returns_nowgoal_row(data_dir):
    """backend/api/routes_public.py::match_odds 里的 xref 查询(routes_public.py 里
    紧跟 `_require_league_access` 之后那条)。"""
    conn = connect_rw("odds")
    try:
        _seed_colliding_xrefs(conn)
        xref = conn.execute(
            "SELECT * FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'"
            " AND review_status IN ('auto_ok','confirmed')",
            (FOTMOB_MATCH_ID,),
        ).fetchone()
        assert xref is not None
        assert xref["provider"] == "nowgoal"
        assert xref["provider_match_id"] == NOWGOAL_PROVIDER_MATCH_ID

        # 拿这个 provider_match_id 去查 bronze_ng_odds_snap 必须命中真实种下的那一行,
        # 不是空结果(证明"修了 xref 过滤但后续查询用错了 provider_match_id"这种
        # 半吊子修复不会被漏检)。
        rows = conn.execute(
            "SELECT * FROM bronze_ng_odds_snap WHERE provider_match_id=?",
            (xref["provider_match_id"],),
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_studio_bundle_xref_query_returns_nowgoal_row(data_dir):
    """backend/studio/bundle.py 里的 xref 查询(odds_timeline/cooccurring_events 段)。"""
    conn = connect_rw("odds")
    try:
        _seed_colliding_xrefs(conn)
        xref = conn.execute(
            "SELECT * FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'"
            " AND review_status IN ('auto_ok','confirmed')",
            (FOTMOB_MATCH_ID,),
        ).fetchone()
        assert xref is not None and xref["provider"] == "nowgoal"
    finally:
        conn.close()


def test_content_pipeline_poll_decision_xref_query_returns_nowgoal_row(data_dir):
    """backend/content_pipeline.py 里判断"是否已有 nowgoal 观测"用的 xref 查询
    (fotmob 赛程驱动的那一处,决定要不要继续轮询 nowgoal)。"""
    conn = connect_rw("odds")
    try:
        _seed_colliding_xrefs(conn)
        xref = conn.execute(
            "SELECT provider_match_id FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'",
            (FOTMOB_MATCH_ID,),
        ).fetchone()
        assert xref is not None
        assert xref["provider_match_id"] == NOWGOAL_PROVIDER_MATCH_ID
    finally:
        conn.close()


def test_content_pipeline_previous_titan_xref_query_returns_nowgoal_row(data_dir):
    """backend/content_pipeline.py 里 _discover_nowgoal_match 之后查"之前映射过的
    titan_id"用的 xref 查询。"""
    conn = connect_rw("odds")
    try:
        _seed_colliding_xrefs(conn)
        xref = conn.execute(
            "SELECT provider_match_id FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'",
            (FOTMOB_MATCH_ID,),
        ).fetchone()
        previous_titan = str(xref["provider_match_id"]) if xref else None
        assert previous_titan == NOWGOAL_PROVIDER_MATCH_ID
    finally:
        conn.close()


def test_match_content_filter_join_only_counts_nowgoal_xref(data_dir):
    """backend/api/routes_public.py 里 content=odds 用的 JOIN
    (dim_match_xref x JOIN bronze_ng_odds_snap b ON provider_match_id)。
    kbisai 行的 provider_match_id 和真实 bronze_ng_odds_snap 里的行对不上,
    但如果 JOIN 不按 provider 过滤,理论上仍可能因为数字撞车而引入假阳性——
    这里显式验证 JOIN 只统计 provider='nowgoal' 的映射。"""
    conn = connect_rw("odds")
    try:
        _seed_colliding_xrefs(conn)
        odds_match_ids = {
            int(row[0])
            for row in conn.execute(
                """SELECT DISTINCT x.fotmob_match_id
                     FROM dim_match_xref x
                     JOIN bronze_ng_odds_snap b
                       ON b.provider_match_id=x.provider_match_id
                    WHERE x.provider='nowgoal'
                      AND x.review_status IN ('auto_ok','confirmed')"""
            )
        }
        assert odds_match_ids == {FOTMOB_MATCH_ID}
    finally:
        conn.close()
