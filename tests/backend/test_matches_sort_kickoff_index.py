"""/api/v1/matches 排序与时间窗口走索引(2026-09-27,迁移 0023)的正确性回归。

背景见 backend/migrations/core/0023_dim_match_sort_kickoff.sql 头注释:
list_matches() 的 ORDER BY 从计算表达式 julianday(COALESCE(kickoff_at_utc,
Date)) 换成新增的 VIRTUAL 生成列 sort_kickoff_utc(值不变,只是有了列名可以
建索引),窗口 WHERE 新增一组冗余谓词(不改变结果集,只帮规划器识别索引
范围 SEEK)。本文件只关心"结果集与排序顺序有没有变",不关心具体走没走
索引(具体走没走索引的基准数据见任务对话与 docs/current-state.md,那属于
一次性诊断,不适合钉成单测——生产数据量、ANALYZE 统计信息才是决定因素,
测试库这么小的数据量下 SQLite 大概率仍会选旧计划,断言"必须走索引"反而是
测试在断言实现细节而不是行为)。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.db.connections import connect_rw
from tests.backend.coreseed import insert_match, seed_core_schema


@pytest.fixture
def mixed_kickoff_matches(data_dir):
    """故意乱序插入,且混入"只有自然日、没有精确开球时间"的比赛(kickoff_at_utc
    为 NULL,sort_kickoff_utc 应回落到 Date)——覆盖 CLAUDE.md §6.2.1 的
    COALESCE 语义,不是随便挑的边界。"""
    conn = connect_rw("core")
    seed_core_schema(conn)
    # 故意乱序插入,验证排序不依赖插入顺序或 Match_ID 大小
    insert_match(conn, 8003, league_id=47, date="2026-10-05", status="NotStarted",
                 kickoff_at_utc="2026-10-05T15:00:00Z")
    insert_match(conn, 8001, league_id=47, date="2026-10-01", status="NotStarted",
                 kickoff_at_utc="2026-10-01T12:00:00Z")
    # 只有自然日、没有精确开球时间(kickoff_at_utc=NULL)——sort_kickoff_utc
    # 回落到 Date="2026-10-03",应排在 8001(10-01)之后、8003(10-05 15:00)之前。
    insert_match(conn, 8002, league_id=47, date="2026-10-03", status="NotStarted",
                 kickoff_at_utc=None)
    insert_match(conn, 8004, league_id=47, date="2026-10-05", status="NotStarted",
                 kickoff_at_utc="2026-10-05T09:00:00Z")  # 与 8003 同一天,更早
    conn.commit()
    conn.close()
    return data_dir


class TestSortOrderUnchanged:
    """排序结果与改动前逐条一致:直接断言"期望顺序",不是拿旧代码跑一遍再
    diff(旧表达式已经从代码里删掉,没有"旧代码"可以再跑)——期望顺序是按
    COALESCE(kickoff_at_utc, Date) 手算出来的,这条语义本身没有变,变的只是
    SQL 里怎么表达它。"""

    def test_ascending_order_with_null_kickoff_fallback(self, app, mixed_kickoff_matches):
        c = TestClient(app)
        r = c.get("/api/v1/matches?league_id=47&status=upcoming&window=all")
        assert r.status_code == 200
        ids = [m["match_id"] for m in r.json()["matches"]]
        # 期望顺序:8001(10-01 12:00) < 8002(10-03,仅自然日)
        #         < 8004(10-05 09:00) < 8003(10-05 15:00)
        assert ids == [8001, 8002, 8004, 8003]

    def test_descending_order_for_finished_status(self, app, mixed_kickoff_matches):
        conn = connect_rw("core")
        conn.execute("UPDATE dim_match SET status='Finish' WHERE Match_ID IN (8001,8002,8003,8004)")
        conn.commit()
        conn.close()
        c = TestClient(app)
        r = c.get("/api/v1/matches?league_id=47&status=finished&window=all")
        assert r.status_code == 200
        ids = [m["match_id"] for m in r.json()["matches"]]
        assert ids == [8003, 8004, 8002, 8001]  # 倒序,与升序完全相反

    def test_window_filter_boundary_semantics_unchanged(self, data_dir):
        """新增的冗余谓词必须与原 julianday(kickoff_at_utc) 谓词同真同假,
        包括半开区间的边界——起点闭区间、终点开区间(与
        backend/queries/matches.py::_window_bounds 文档化的语义一致)。
        直接对着 SQL 断言边界,比经过 _window_bounds 的自然日/滚动窗口换算
        更精确、不受"今天几号"影响。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8101, league_id=47, date="2026-09-27", status="NotStarted",
                     kickoff_at_utc="2026-09-27T00:00:00Z")   # 恰好等于 start:闭区间应包含
        insert_match(conn, 8102, league_id=47, date="2026-09-28", status="NotStarted",
                     kickoff_at_utc="2026-09-28T00:00:00Z")   # 恰好等于 end:开区间应排除
        insert_match(conn, 8103, league_id=47, date="2026-09-26", status="NotStarted",
                     kickoff_at_utc="2026-09-26T23:59:59Z")   # start 前 1 秒:排除
        conn.commit()

        cur = conn.execute(
            """SELECT Match_ID FROM dim_match
               WHERE League_ID=47
                 AND julianday(kickoff_at_utc) >= julianday(?)
                 AND julianday(kickoff_at_utc) < julianday(?)
                 AND sort_kickoff_utc >= ? AND sort_kickoff_utc < ?
               ORDER BY sort_kickoff_utc ASC""",
            ("2026-09-27T00:00:00Z", "2026-09-28T00:00:00Z",
             "2026-09-27T00:00:00Z", "2026-09-28T00:00:00Z"),
        )
        ids = [r[0] for r in cur.fetchall()]
        conn.close()
        assert ids == [8101]  # 起点闭区间含 8101,终点开区间排除 8102,8103 在窗口之前

    def test_list_matches_direct_call_respects_window_with_real_time_source(self, data_dir):
        """经 backend.queries.matches.list_matches() 本身(不经 HTTP 层)验证
        window 参数的注入 now 仍然只筛出窗口内的比赛,双路谓词(julianday +
        sort_kickoff_utc)同真同假。"""
        from backend.queries import matches as q_matches

        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8401, league_id=47, date="2026-09-27", status="NotStarted",
                     kickoff_at_utc="2026-09-28T10:00:00Z")   # 窗口内(未来 1 天)
        insert_match(conn, 8402, league_id=47, date="2026-09-27", status="NotStarted",
                     kickoff_at_utc="2026-10-10T10:00:00Z")   # 窗口外(远超 7 天)
        conn.commit()

        result = q_matches.list_matches(
            conn=conn,
            league_ids={47},
            status="upcoming",
            window="7d",
            now=datetime(2026, 9, 27, 0, 0, 0, tzinfo=timezone.utc),
            limit=50,
        )
        conn.close()
        ids = {m["match_id"] for m in result["matches"]}
        assert 8401 in ids
        assert 8402 not in ids


class TestGeneratedColumnCorrectness:
    def test_sort_kickoff_utc_falls_back_to_date_when_kickoff_null(self, data_dir):
        conn = connect_rw("core")
        seed_core_schema(conn)
        insert_match(conn, 8201, league_id=47, date="2026-11-11", kickoff_at_utc=None)
        insert_match(conn, 8202, league_id=47, date="2026-11-12", kickoff_at_utc="2026-11-12T18:30:00Z")
        conn.commit()
        rows = conn.execute(
            "SELECT Match_ID, sort_kickoff_utc FROM dim_match WHERE Match_ID IN (8201, 8202) ORDER BY Match_ID"
        ).fetchall()
        conn.close()
        assert tuple(rows[0]) == (8201, "2026-11-11")
        assert tuple(rows[1]) == (8202, "2026-11-12T18:30:00Z")

    def test_generated_column_hidden_from_pragma_table_info_but_present_in_select_star(self, data_dir):
        """如实钉住这条容易踩的边界:PRAGMA table_info 会隐藏生成列(SQLite
        约定),依赖它动态拼列名的写路径(如 active_league_mvp.py 的
        _insert_match)天然不会尝试写这一列;但 Python sqlite3.Row 的
        `SELECT *` 仍会把它作为普通字段带出来——backend/queries/matches.py
        的三处 SELECT * FROM dim_match 消费方式都是按列名精确取字段
        (_row_to_summary 等),多一个不读的字段无影响,这条测试只是把"两种
        API 行为不一致"这个事实钉住,不让未来的人凭直觉误判。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        table_info_cols = {r[1] for r in conn.execute("PRAGMA table_info(dim_match)")}
        assert "sort_kickoff_utc" not in table_info_cols

        conn.row_factory = sqlite3.Row
        insert_match(conn, 8301, league_id=47, date="2026-12-01", kickoff_at_utc="2026-12-01T10:00:00Z")
        conn.commit()
        row = conn.execute("SELECT * FROM dim_match WHERE Match_ID=8301").fetchone()
        conn.close()
        assert "sort_kickoff_utc" in row.keys()
        assert row["sort_kickoff_utc"] == "2026-12-01T10:00:00Z"
