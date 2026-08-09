"""poll_kbisai_odds CLI 回归(本轮任务 §8c/§9)。

网络层用 monkeypatch 替身(同 test_kbisai_live_scores.py::TestCliArtifacts 的
写法),只验证:xref 缺失时如实跳过、请求预算硬上限、落库计数、幂等重跑、
artifact 落盘。真实网络请求已经在 test_kbisai_odds.py / 手动交叉验证里覆盖过。
"""

import json
from pathlib import Path

import pytest

from backend.cli import poll_kbisai_odds as cli
from backend.db.connections import connect_rw

FOTMOB_MATCH_ID = 5104970
KBISAI_MATCH_ID = "4467576"

# 真实结构形状(公司 id 是字符串键),内容参照真实观测到的样例改写,标注在此。
FAKE_ASIA_ODDS = {
    "7": {"statusMatchOdds": [
        {"oddsInfo": ["0.99", "0.5", "0.79", "0"], "changeTime": 1785900000,
         "goingTime": "", "score": "0-0", "statusId": 1},
        {"oddsInfo": ["0.79", "0.25", "0.99", "0"], "changeTime": 1785800000,
         "goingTime": "", "score": "0-0", "statusId": 1},
    ]},
    "22": {"statusMatchOdds": [
        {"oddsInfo": ["1.04", "0.5", "0.83", "0"], "changeTime": 1785900500,
         "goingTime": "", "score": "0-0", "statusId": 1},
    ]},
    "999": {"statusMatchOdds": [
        {"oddsInfo": ["1.9", "-0.25", "1.9", "0"], "changeTime": 1785900000,
         "goingTime": "", "score": "0-0", "statusId": 1},
    ]},   # 非目标公司,必须被过滤掉
}
FAKE_EU_ODDS = {
    "7": {"statusMatchOdds": [
        {"oddsInfo": ["1.5", "3.8", "5.0", "0"], "changeTime": 1785900000,
         "goingTime": "", "score": "0-0", "statusId": 1},
    ]},
}
FAKE_COMPANY_NAMES = {"7": "澳*", "22": "平*", "2": "36*", "999": "某个非目标公司"}


def _fake_fetch_match_all_odds(match_id, market, **_kwargs):
    assert str(match_id) == KBISAI_MATCH_ID
    if market == "asia":
        return FAKE_ASIA_ODDS
    if market == "eu":
        return FAKE_EU_ODDS
    return {}


@pytest.fixture
def seeded(data_dir):
    conn = connect_rw("odds")
    conn.execute(
        """INSERT INTO dim_match_xref
           (fotmob_match_id, provider, provider_match_id, confidence, verified,
            method, review_status, created_at, updated_at)
           VALUES (?, 'kbisai', ?, 0.7, 0, 'auto', 'needs_review',
                   '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z')""",
        (FOTMOB_MATCH_ID, KBISAI_MATCH_ID),
    )
    conn.commit()
    conn.close()

    core = connect_rw("core")
    core.execute(
        """INSERT INTO dim_match
               (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
                Home_Team_Name, Away_Team_Name, status, Match_Round,
                kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, '2026', 59, '2026-08-07', 8007, 8478, 'Sandefjord', 'KFUM',
                   'NotStarted', '17', '2026-08-07T17:00:00Z', 'exact', 'fotmob:fixtures')""",
        (FOTMOB_MATCH_ID,),
    )
    core.commit()
    core.close()
    return data_dir


def test_skips_unmapped_match_id_without_any_request(seeded, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "fetch_all_companies", lambda **_k: (calls.append("companies"), FAKE_COMPANY_NAMES)[1])
    monkeypatch.setattr(cli, "fetch_match_all_odds", lambda *a, **k: (calls.append(a), _fake_fetch_match_all_odds(*a, **k))[1])

    summary = cli.run(
        fotmob_match_ids=[999999],   # 没有 xref 映射
        markets=("asia",),
        output_dir=Path("runtime/research/kbisai-odds-test"),
        sleep_fn=lambda _s: None,
    )
    assert summary["unmapped_match_ids"] == [999999]
    assert summary["per_match"][0]["status"] == "SKIPPED_NO_XREF"
    assert summary["totals"] == {"inserted": 0, "duplicate": 0, "rejected_constraint": 0}
    # 除了 fetch_all_companies 的那一次,不应该为一个没映射的比赛发起任何 matchAllOdds 请求。
    assert all(c == "companies" for c in calls)


def test_collects_and_filters_to_target_companies_only(seeded, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "fetch_all_companies", lambda **_k: FAKE_COMPANY_NAMES)
    monkeypatch.setattr(cli, "fetch_match_all_odds", _fake_fetch_match_all_odds)

    summary = cli.run(
        fotmob_match_ids=[FOTMOB_MATCH_ID],
        company_ids=("2", "7", "22"),
        markets=("asia", "eu"),
        output_dir=tmp_path,
        sleep_fn=lambda _s: None,
    )
    assert summary["status"] == "OK"
    entry = summary["per_match"][0]
    assert entry["provider_match_id"] == KBISAI_MATCH_ID

    asia = entry["markets"]["asia"]
    assert asia["companies_present"] == ["22", "7"]
    assert asia["companies_missing"] == ["2"]
    assert asia["parsed_rows"] == 3   # company 7: 2 points + company 22: 1 point,999 被过滤掉
    assert asia["inserted"] == 3
    assert asia["rejected_constraint"] == 0

    eu = entry["markets"]["eu"]
    assert eu["companies_present"] == ["7"]
    assert eu["parsed_rows"] == 1

    assert summary["totals"]["inserted"] == 4

    conn = connect_rw("odds")
    rows = conn.execute(
        "SELECT market, company_id, handicap_line, market_phase FROM bronze_kbisai_odds_point"
        " ORDER BY market, company_id, source_updated_at"
    ).fetchall()
    conn.close()
    assert len(rows) == 4
    # 目标公司之外的 999 不应该出现在库里。
    assert all(r["company_id"] != "999" for r in rows)
    ah_rows = [r for r in rows if r["market"] == "ah"]
    assert all(r["handicap_line"] is not None for r in ah_rows)
    # kickoff 已知且 statusId=1(NOT_STARTED)、changeTime 早于 kickoff -> pre_match。
    assert all(r["market_phase"] == "pre_match" for r in rows)


def test_idempotent_rerun_produces_zero_new_inserts(seeded, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "fetch_all_companies", lambda **_k: FAKE_COMPANY_NAMES)
    monkeypatch.setattr(cli, "fetch_match_all_odds", _fake_fetch_match_all_odds)

    first = cli.run(
        fotmob_match_ids=[FOTMOB_MATCH_ID], markets=("asia",),
        output_dir=tmp_path, sleep_fn=lambda _s: None,
    )
    assert first["totals"]["inserted"] == 3

    second = cli.run(
        fotmob_match_ids=[FOTMOB_MATCH_ID], markets=("asia",),
        output_dir=tmp_path, sleep_fn=lambda _s: None,
    )
    assert second["totals"]["inserted"] == 0
    assert second["totals"]["duplicate"] == 3

    conn = connect_rw("odds")
    count = conn.execute("SELECT COUNT(*) FROM bronze_kbisai_odds_point").fetchone()[0]
    conn.close()
    assert count == 3


def test_request_budget_aborts_before_any_request(seeded, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "fetch_all_companies", lambda **_k: (calls.append(1), FAKE_COMPANY_NAMES)[1])
    monkeypatch.setattr(cli, "fetch_match_all_odds", lambda *a, **k: (calls.append(1), _fake_fetch_match_all_odds(*a, **k))[1])

    summary = cli.run(
        fotmob_match_ids=[FOTMOB_MATCH_ID],
        markets=("eu", "asia", "bs"),
        max_requests=1,   # 1 场 * 3 market + 1(allCompany) = 4 > 1
        output_dir=tmp_path,
        sleep_fn=lambda _s: None,
    )
    assert summary["status"] == "ABORTED_BUDGET"
    assert summary["requests_made"] == 0
    assert calls == []   # 没有发起任何一次请求


def test_artifacts_are_written_with_private_permissions(seeded, monkeypatch, tmp_path):
    import stat

    monkeypatch.setattr(cli, "fetch_all_companies", lambda **_k: FAKE_COMPANY_NAMES)
    monkeypatch.setattr(cli, "fetch_match_all_odds", _fake_fetch_match_all_odds)

    summary = cli.run(
        fotmob_match_ids=[FOTMOB_MATCH_ID], markets=("asia",),
        output_dir=tmp_path, sleep_fn=lambda _s: None,
    )
    run_dir = tmp_path / summary["run_id"]
    on_disk = json.loads((run_dir / "summary.json").read_text())
    assert on_disk == summary
    raw_file = run_dir / f"raw-{FOTMOB_MATCH_ID}-asia.json"
    assert json.loads(raw_file.read_text()) == FAKE_ASIA_ODDS
    for path in run_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
