"""Season 归属的写路径契约(2026-08-25 二次收口,CLAUDE.md §6.3)。

第一版契约(当日早些时候)是"season 必填,None 拒绝"——那仍然靠调用方传对,
打错字照样落库。终版按 FotMob 架构(Match 模型没有 Season 字段)收口:

1. parse_match_dim 彻底没有 season 形参、输出没有 Season 键;
2. ingest_match 彻底没有 season 形参;dim_match 行必须已由赛程同步建好,
   行不存在时在**任何网络请求之前** fail closed;
3. ingest_match 的 dim_match 写入是列作用域 upsert(MATCH_DETAIL_OWNED_COLUMNS,
   不含 Season)——**已有行的 Season 原样保留**,这条直接钉住事故机制
   (整行 INSERT OR REPLACE 曾把赛程同步写对的赛季用手填值冲掉);
4. 存储层由 migrations/core/0011 的触发器兜底(见 test_season_regime.py)。
"""

import inspect
import json
import os

import pytest

from backend.db.connections import connect_rw
from backend.fotmob_client import FotMobClient
from backend.ingest.ingest_match import (
    MATCH_DETAIL_OWNED_COLUMNS,
    ingest_match,
)
from tests.backend.coreseed import insert_match, seed_core_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "fotmob", "prematch-5104961.json"
)


class TestParseMatchDimHasNoSeason:
    def test_no_season_parameter(self):
        assert "season" not in inspect.signature(
            FotMobClient.parse_match_dim
        ).parameters

    def test_output_has_no_season_key(self):
        row = FotMobClient(proxy="").parse_match_dim(
            {"general": {}, "header": {}, "content": {}}, match_id=1
        )
        assert "Season" not in row


class TestIngestMatchSeasonOwnership:
    def test_no_season_parameter(self):
        assert "season" not in inspect.signature(ingest_match).parameters

    def test_owned_columns_exclude_season(self):
        assert "Season" not in MATCH_DETAIL_OWNED_COLUMNS
        assert "Match_ID" not in MATCH_DETAIL_OWNED_COLUMNS
        assert "status" in MATCH_DETAIL_OWNED_COLUMNS  # 明细确实拥有其它列

    def test_missing_row_fails_closed_before_any_network(self, data_dir, monkeypatch):
        """行不存在 → 在构造 FotMobClient(即任何网络动作)之前就拒绝。"""
        conn = connect_rw("core")
        seed_core_schema(conn)
        conn.commit()
        conn.close()
        monkeypatch.setattr(
            "backend.ingest.ingest_match.FotMobClient",
            lambda: (_ for _ in ()).throw(AssertionError("不该构造 FotMobClient")),
        )
        with pytest.raises(ValueError, match="没有这场比赛的赛程行"):
            ingest_match(5104961, league_id=59)

    def test_existing_season_survives_reingest(self, data_dir, monkeypatch):
        """事故机制回归:预置一行赛程同步写好的 Season,跑 ingest_match,
        断言 Season 原样不动、明细列(场馆等)确实被更新——此前的整行
        INSERT OR REPLACE 会在这里把 Season 冲掉。"""
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            payload = json.load(f)

        conn = connect_rw("core")
        seed_core_schema(conn)
        # 赛程同步先建行:挪超(59,自然年)2026 赛季
        insert_match(conn, 5104961, league_id=59, date="2026-08-01",
                     home_id=111, away_id=222, home="H", away="A",
                     status="NotStarted")
        row = conn.execute(
            "SELECT Season FROM dim_match WHERE Match_ID=5104961"
        ).fetchone()
        assert row["Season"] == "2026"  # 推导种子(见 coreseed._AUTO_SEASON)
        conn.commit()
        conn.close()

        parse_client = FotMobClient(proxy="")

        class _FakeClient:
            def match_details(self, match_id):
                return payload

            def __getattr__(self, name):
                return getattr(parse_client, name)

        monkeypatch.setattr(
            "backend.ingest.ingest_match.FotMobClient", lambda: _FakeClient()
        )
        ingest_match(5104961, league_id=59, date="2026-08-01")

        conn = connect_rw("core")
        row = conn.execute(
            "SELECT Season, Venue_Name, status FROM dim_match WHERE Match_ID=5104961"
        ).fetchone()
        conn.close()
        # Season 原封不动(明细路径不拥有它)
        assert row["Season"] == "2026"
        # 明细列真的写进去了(fixture 是真实挪超 payload,带场馆)
        assert row["Venue_Name"] is not None
