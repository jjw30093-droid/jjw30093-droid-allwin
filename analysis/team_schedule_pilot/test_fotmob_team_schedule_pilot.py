"""离线单测:analysis/team_schedule_pilot。不访问网络,不要求 THORDATA_PROXY。

覆盖 docs/audits/team-schedule-pilot.md 引用的十六项规则 + 分类/CLI/凭证防护。
"""

import importlib
import json
import logging
import os
import sqlite3
import sys
import traceback

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from analysis.team_schedule_pilot.fotmob_team_schedule_pilot import (  # noqa: E402
    ScheduleConflictError,
    ScheduleSchemaError,
    _parse_strict_positive_int,
    classify_competition,
    compute_rest_hours,
    derive_kickoff,
    find_cross_comp_rest_examples,
    init_pilot_db,
    parse_team_schedule_response,
    run_cli,
    write_match_calendar,
    write_team_match,
)

TEAM_ID = 8456
FIXTURE_PATH = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "fotmob", "team_schedule_pilot_minimal.json"
)


def _wrap(fixtures_list):
    return {"fixtures": {"allFixtures": {"fixtures": fixtures_list}}}


def _status(utc, finished=True, cancelled=False, started=True, date_tbd=None, time_tbd=None):
    s = {"utcTime": utc, "finished": finished, "cancelled": cancelled, "started": started}
    if date_tbd is not None:
        s["matchDateTbd"] = date_tbd
    if time_tbd is not None:
        s["matchTimeTbd"] = time_tbd
    return s


def _fixture(id_, home_id, away_id, utc, tournament_name="Premier League", league_id=47,
             finished=True, cancelled=False, not_started=False, **status_kwargs):
    return {
        "id": id_,
        "home": {"id": home_id, "name": f"home{home_id}"},
        "away": {"id": away_id, "name": f"away{away_id}"},
        "notStarted": not_started,
        "tournament": {"name": tournament_name, "stage": "", "leagueId": league_id},
        "status": _status(utc, finished=finished, cancelled=cancelled, **status_kwargs),
    }


# ── 1. 正确解析主场比赛 ──────────────────────────────────────────────────

def test_parses_home_match_correctly():
    raw = _wrap([_fixture(1001, home_id=TEAM_ID, away_id=999, utc="2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert len(records) == 1
    r = records[0]
    assert r["is_home"] is True
    assert r["home_team_id"] == TEAM_ID
    assert r["opponent_team_id"] == 999


# ── 2. 正确解析客场比赛 ──────────────────────────────────────────────────

def test_parses_away_match_correctly():
    raw = _wrap([_fixture(1002, home_id=999, away_id=TEAM_ID, utc="2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert len(records) == 1
    r = records[0]
    assert r["is_home"] is False
    assert r["away_team_id"] == TEAM_ID
    assert r["opponent_team_id"] == 999


# ── 3. requested_team_id 不在主客双方时拒绝 ──────────────────────────────

def test_rejects_match_where_requested_team_not_involved():
    raw = _wrap([_fixture(1003, home_id=111, away_id=222, utc="2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records == []


# ── 4. Match ID 去重 ─────────────────────────────────────────────────────

def test_dedupes_identical_duplicate_match_id():
    f = _fixture(1004, home_id=TEAM_ID, away_id=999, utc="2026-01-01T15:00:00.000Z")
    raw = _wrap([f, dict(f)])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert len(records) == 1


# ── 5. 同 Match ID 语义冲突时拒绝 ─────────────────────────────────────────

def test_conflicting_duplicate_match_id_raises():
    f1 = _fixture(1005, home_id=TEAM_ID, away_id=999, utc="2026-01-01T15:00:00.000Z")
    f2 = _fixture(1005, home_id=TEAM_ID, away_id=888, utc="2026-01-01T15:00:00.000Z")  # 对手不同
    raw = _wrap([f1, f2])
    with pytest.raises(ScheduleConflictError):
        parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")


# ── 6. exact UTC 正确标准化 ───────────────────────────────────────────────

def test_exact_utc_normalized():
    utc, precision = derive_kickoff({"utcTime": "2026-04-22T19:00:00.000Z", "finished": True})
    assert precision == "exact"
    assert utc == "2026-04-22T19:00:00Z"


# ── 7. date_only 不补午夜 ─────────────────────────────────────────────────

def test_date_only_does_not_fabricate_midnight():
    utc, precision = derive_kickoff({"utcTime": "2026-04-22", "finished": False})
    assert precision == "date_only"
    assert utc is None  # 绝不补 00:00


def test_unknown_when_date_tbd_even_with_full_iso_string():
    """真实数据发现:33/50 场未开赛比赛 matchDateTbd=true,即便带着一个完整
    ISO 占位字符串,来源本身声明日期未定——不能算 date_only(那样等于假装
    日期可信),只能算 unknown。"""
    utc, precision = derive_kickoff({
        "utcTime": "2026-10-10T14:00:00.000Z", "finished": False,
        "matchDateTbd": True, "matchTimeTbd": False,
    })
    assert precision == "unknown"
    assert utc is None


# ── 8. cancelled 不进入 rest ──────────────────────────────────────────────

def test_cancelled_excluded_from_rest_calculation():
    raw = _wrap([
        _fixture(2001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _fixture(2002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z", cancelled=True, finished=False),
        _fixture(2003, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    rest = compute_rest_hours(records)
    assert len(rest) == 2  # 只有 2001、2003 参与(2002 被 cancelled 排除)
    assert {r["provider_match_id"] for r in rest} == {2001, 2003}
    last = next(r for r in rest if r["provider_match_id"] == 2003)
    assert last["previous_match_id"] == 2001  # 不是被排除的 2002
    assert last["rest_hours"] == pytest.approx(9 * 24.0)


# ── 9. unfinished 不作为上一场比赛 ────────────────────────────────────────

def test_unfinished_match_not_counted_as_previous():
    raw = _wrap([
        _fixture(3001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _fixture(3002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z", finished=False, not_started=True),
        _fixture(3003, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    rest = compute_rest_hours(records)
    assert len(rest) == 2
    last = next(r for r in rest if r["provider_match_id"] == 3003)
    assert last["previous_match_id"] == 3001


# ── 10. all-comp 中间插入比赛会缩短下一场 league rest(真实样本回归) ──────

def test_cross_competition_match_shortens_next_league_rest():
    """复现 2026-07-23 真实观测(Team ID=8456):
    PL 04-22 → FA Cup 04-25 → PL 05-04,足总杯把"上一场任意正式比赛"的时间点
    从 04-22 推到 04-25,all_comp_rest_hours(218.75h) < league_only_rest_hours(288h)。
    """
    raw = _wrap([
        _fixture(4813708, 8191, TEAM_ID, "2026-04-22T19:00:00.000Z"),
        _fixture(5293793, TEAM_ID, 8466, "2026-04-25T16:15:00.000Z",
                 tournament_name="FA Cup", league_id=132),
        _fixture(4813720, 8668, TEAM_ID, "2026-05-04T19:00:00.000Z"),
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    rest = compute_rest_hours(records)
    examples = find_cross_comp_rest_examples(rest)
    assert len(examples) == 1
    ex = examples[0]
    assert ex["provider_match_id"] == 4813720
    assert ex["all_comp_rest_hours"] == pytest.approx(218.75)
    assert ex["league_only_rest_hours"] == pytest.approx(288.0)
    assert ex["diff_hours"] == pytest.approx(288.0 - 218.75)
    assert ex["all_comp_rest_hours"] < ex["league_only_rest_hours"]


# ── 11. 7 天/14 天比赛数边界 ──────────────────────────────────────────────

def test_matches_last_7d_14d_boundaries():
    # anchor: 2026-02-01T00:00:00Z;-14d 整/-7d 整均应计入(边界含),更早的不计入
    raw = _wrap([
        _fixture(5002, TEAM_ID, 999, "2026-01-18T00:00:00.000Z"),  # exactly -14d
        _fixture(5003, TEAM_ID, 999, "2026-01-25T00:00:00.000Z"),  # exactly -7d
        _fixture(5004, TEAM_ID, 999, "2026-02-01T00:00:00.000Z"),  # anchor
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    rest = compute_rest_hours(records)
    anchor = next(r for r in rest if r["provider_match_id"] == 5004)
    assert anchor["matches_last_14d"] == 2   # 5002(边界=14d,含)、5003
    assert anchor["matches_last_7d"] == 1    # 只有 5003(边界=7d,含)


def test_matches_last_7d_excludes_just_outside_boundary():
    raw = _wrap([
        _fixture(5101, TEAM_ID, 999, "2026-01-24T23:59:59.000Z"),  # -7d-1s -> 不计入 7d
        _fixture(5102, TEAM_ID, 999, "2026-02-01T00:00:00.000Z"),  # anchor
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    rest = compute_rest_hours(records)
    anchor = next(r for r in rest if r["provider_match_id"] == 5102)
    assert anchor["matches_last_7d"] == 0


# ── 12. 幂等写入临时 SQLite ───────────────────────────────────────────────

def test_idempotent_sqlite_write(tmp_path):
    raw = _wrap([
        _fixture(6001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _fixture(6002, 999, TEAM_ID, "2026-01-08T15:00:00.000Z"),
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")

    db_path = str(tmp_path / "pilot.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    r1 = write_match_calendar(conn, records)
    t1 = write_team_match(conn, records)
    assert r1 == {"inserted": 2, "skipped": 0}
    assert t1 == {"inserted": 2, "skipped": 0}

    r2 = write_match_calendar(conn, records)
    t2 = write_team_match(conn, records)
    assert r2 == {"inserted": 0, "skipped": 2}
    assert t2 == {"inserted": 0, "skipped": 2}

    n_cal = conn.execute("SELECT COUNT(*) FROM pilot_match_calendar").fetchone()[0]
    n_team = conn.execute("SELECT COUNT(*) FROM pilot_team_match").fetchone()[0]
    assert n_cal == 2
    assert n_team == 2
    conn.close()


def test_sqlite_write_conflict_raises(tmp_path):
    raw1 = _wrap([_fixture(6101, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    raw2 = _wrap([_fixture(6101, TEAM_ID, 888, "2026-01-01T15:00:00.000Z")])  # 对手换了

    records1 = parse_team_schedule_response(raw1, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    records2 = parse_team_schedule_response(raw2, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")

    db_path = str(tmp_path / "pilot_conflict.db")
    conn = sqlite3.connect(db_path)
    init_pilot_db(conn)
    write_match_calendar(conn, records1)
    with pytest.raises(ScheduleConflictError):
        write_match_calendar(conn, records2)
    conn.close()


# ── 13. fixture/日志中不含 THORDATA_PROXY 或常见凭证格式 ──────────────────

def test_fixture_file_contains_no_credentials():
    """检查的是真实凭证值的形状(user:pass@host),不是环境变量名本身——
    fixture 说明文字里提到 "不含 THORDATA_PROXY" 是合法的透明披露,不是泄露。"""
    with open(FIXTURE_PATH) as f:
        text = f.read()
    assert "THORDATA_PROXY=" not in text  # 真正的赋值/取值才算泄露
    # 常见 user:pass@host 代理 URL 形状:不能同时出现协议头与显式账号密码
    assert not ("://" in text and "@" in text)


def test_redact_check_blocks_credential_like_output():
    from analysis.team_schedule_pilot.fotmob_team_schedule_pilot import _redact_check
    with pytest.raises(RuntimeError):
        _redact_check("THORDATA_PROXY=http://user:pass@1.2.3.4:8080")
    with pytest.raises(RuntimeError):
        _redact_check('{"proxy": "http://someuser:secret@proxyhost:8080"}')
    _redact_check(json.dumps({"team_id": 8456, "note": "no secrets here"}))  # 不应抛错


# ── 14. 负数或倒序 rest 不得产生 ──────────────────────────────────────────

def test_no_negative_or_reversed_rest_even_with_unsorted_input():
    raw = _wrap([
        _fixture(7003, TEAM_ID, 999, "2026-01-10T15:00:00.000Z"),  # 故意乱序:先给最晚的
        _fixture(7001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z"),
        _fixture(7002, TEAM_ID, 999, "2026-01-05T15:00:00.000Z"),
    ])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    rest = compute_rest_hours(records)
    for r in rest:
        if r["rest_hours"] is not None:
            assert r["rest_hours"] >= 0
    by_id = {r["provider_match_id"]: r for r in rest}
    assert by_id[7001]["rest_hours"] is None  # 窗口内第一场,无更早数据
    assert by_id[7002]["rest_hours"] == pytest.approx(4 * 24.0)
    assert by_id[7003]["rest_hours"] == pytest.approx(5 * 24.0)


# ── 15. 缺少 competition_id 时不伪造来源 ID ───────────────────────────────

def test_missing_competition_id_not_fabricated():
    f = _fixture(8001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    del f["tournament"]["leagueId"]
    raw = _wrap([f])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records[0]["competition_id"] is None


# ── A1. fixtures 非 dict 元素 → 明确抛 ScheduleSchemaError,不裸 AttributeError ──

@pytest.mark.parametrize("bad_element", [None, "not-a-dict", [1, 2, 3], 42])
def test_non_dict_fixture_element_raises_schema_error_not_bare_attribute_error(bad_element):
    raw = _wrap([bad_element])
    with pytest.raises(ScheduleSchemaError):
        parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")


def test_non_dict_fixture_element_does_not_produce_partial_schedule():
    """坏元素必须让整批解析 fail-loud,不能"跳过坏元素、悄悄吐出剩下几条"。"""
    good = _fixture(1101, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    raw = _wrap([good, None])
    with pytest.raises(ScheduleSchemaError):
        parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")


# ── A2. 严格 Match ID / team ID / competition_id 解析 ────────────────────

@pytest.mark.parametrize("value,expected", [
    (4813720, 4813720),
    ("4813720", 4813720),
    (9.0, None),        # float(即便整数值)一律拒绝
    (9.9, None),
    (True, None),       # bool 不当整数
    (False, None),
    (-5, None),          # 负数
    ("-5", None),
    (0, None),           # 0
    ("0", None),
    ("9.9", None),       # 小数点字符串
    ("1e10", None),      # 科学计数法字符串
    ("", None),          # 空字符串
    ("abc", None),       # 非数字字符串
    (None, None),
])
def test_parse_strict_positive_int(value, expected):
    assert _parse_strict_positive_int(value) == expected


def test_float_match_id_rejected_not_silently_truncated():
    """int(9.9)=9 的静默截断是被明确禁止的行为——float 型 id 必须被拒绝整条记录,
    不能产生一条 provider_match_id=9 的错误记录。"""
    raw = _wrap([_fixture(9.9, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records == []  # 整条记录被拒绝,不出现 provider_match_id=9


def test_bool_match_id_rejected():
    raw = _wrap([_fixture(True, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records == []


def test_negative_match_id_rejected():
    raw = _wrap([_fixture(-4813720, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records == []


def test_decimal_string_match_id_rejected():
    raw = _wrap([_fixture("4813720.0", TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records == []


def test_float_home_or_away_team_id_not_guessed():
    """requested team 自身的 home/away ID 若是非法类型(float),不得靠 int() 猜测
    截断后当作合法匹配——该场必须被拒绝,不进入结果。"""
    f = _fixture(1201, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    f["home"]["id"] = float(TEAM_ID) + 0.5  # 非法类型,不能截断成 TEAM_ID
    raw = _wrap([f])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records == []


def test_invalid_type_competition_id_not_fabricated_as_valid():
    """来源给了非法类型的 leagueId(如 float)时,competition_id 必须落 None,
    不得静默截断成一个看似合法的整数 ID。"""
    f = _fixture(1202, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")
    f["tournament"]["leagueId"] = 47.5
    raw = _wrap([f])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records[0]["competition_id"] is None


# ── 16. went_to_extra_time 无来源证据时为 NULL ────────────────────────────

def test_went_to_extra_time_always_none_when_source_silent():
    raw = _wrap([_fixture(9001, TEAM_ID, 999, "2026-01-01T15:00:00.000Z")])
    records = parse_team_schedule_response(raw, TEAM_ID, "2025/2026", "test", "2026-01-01T00:00:00Z")
    assert records[0]["went_to_extra_time"] is None


# ── 赛事分类(heuristic_name) ──────────────────────────────────────────────

@pytest.mark.parametrize("name,league_id,expected_class", [
    ("Premier League", 47, "league"),
    ("FA Cup", 132, "domestic_cup"),
    ("Community Shield", 247, "super_cup"),
    ("Club Friendlies", 489, "friendly"),
    ("Some Random Trophy Nobody Heard Of", 99999, "other"),
    (None, None, "unknown"),
])
def test_classify_competition_real_observed_names(name, league_id, expected_class):
    cls, method = classify_competition(name, league_id)
    assert cls == expected_class
    assert method == "heuristic_name"


def test_classify_competition_continental_synthetic_case():
    """真实抓取窗口(2026-07-23,team_id=8456)内没有出现任何欧战比赛
    (Champions League 等 UEFA 赛事不在该滚动窗口的日期范围内),因此这里用
    人工构造的 synthetic 名称验证分类逻辑本身,不冒充真实观测数据。"""
    cls, method = classify_competition("UEFA Champions League", 42)
    assert cls == "continental"
    assert method == "heuristic_name"


# ── FotMobClient.team_data() 新增方法(无网络,只测 URL 构造) ──────────────

def test_fotmob_client_import_and_explicit_empty_proxy_need_no_credentials(
    monkeypatch,
):
    monkeypatch.delenv("THORDATA_PROXY", raising=False)
    import dotenv

    dotenv_calls = []

    def unexpected_dotenv(*args, **kwargs):
        dotenv_calls.append((args, kwargs))
        return False

    monkeypatch.setattr(dotenv, "load_dotenv", unexpected_dotenv)
    monkeypatch.delitem(sys.modules, "backend.fotmob_client", raising=False)
    module = importlib.import_module("backend.fotmob_client")

    captured = {}

    class FakeResp:
        def json(self):
            return {"fixtures": {}}

    def fake_get(self, url, headers=None):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(module.FotMobClient, "_get", fake_get)
    client = module.FotMobClient(proxy="")
    assert client.proxies == {}
    assert client.team_data(8456) == {"fixtures": {}}
    assert captured["url"] == "https://www.fotmob.com/api/data/teams?id=8456"
    assert dotenv_calls == []


def test_fotmob_client_existing_environment_skips_dotenv(monkeypatch):
    from backend import fotmob_client as module

    placeholder = "http://offline.invalid:1"
    monkeypatch.setenv("THORDATA_PROXY", placeholder)

    def forbidden_dotenv(*args, **kwargs):
        raise AssertionError("existing THORDATA_PROXY must not read .env")

    monkeypatch.setattr(module, "load_dotenv", forbidden_dotenv)
    client = module.FotMobClient()
    assert client.proxy == placeholder
    assert client.proxies == {"http": placeholder, "https": placeholder}


def test_fotmob_client_explicit_proxy_skips_environment_and_dotenv(monkeypatch):
    from backend import fotmob_client as module

    monkeypatch.delenv("THORDATA_PROXY", raising=False)
    explicit = "http://offline.invalid:2"

    def forbidden_dotenv(*args, **kwargs):
        raise AssertionError("explicit proxy must not read .env")

    monkeypatch.setattr(module, "load_dotenv", forbidden_dotenv)
    client = module.FotMobClient(proxy=explicit)
    assert client.proxy == explicit
    assert client.proxies == {"http": explicit, "https": explicit}


def test_fotmob_client_default_loads_dotenv_only_when_environment_missing(
    monkeypatch,
):
    from backend import fotmob_client as module

    monkeypatch.delenv("THORDATA_PROXY", raising=False)
    dotenv_calls = []
    placeholder = "http://offline.invalid:1"

    def fake_dotenv(*args, **kwargs):
        dotenv_calls.append((args, kwargs))
        monkeypatch.setenv("THORDATA_PROXY", placeholder)
        return True

    monkeypatch.setattr(module, "load_dotenv", fake_dotenv)
    client = module.FotMobClient()
    assert client.proxy == placeholder
    assert len(dotenv_calls) == 1


def test_fotmob_client_missing_default_proxy_error_is_sanitized(monkeypatch):
    from backend import fotmob_client as module

    monkeypatch.delenv("THORDATA_PROXY", raising=False)
    monkeypatch.setattr(module, "load_dotenv", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError) as exc_info:
        module.FotMobClient()
    message = str(exc_info.value)
    assert "THORDATA_PROXY" in message
    assert "://" not in message
    assert "@" not in message


def _exception_and_log_text(exc_info, caplog):
    parts = [
        caplog.text,
        str(exc_info.value),
        repr(exc_info.value),
        "".join(
            traceback.format_exception(
                type(exc_info.value),
                exc_info.value,
                exc_info.value.__traceback__,
            )
        ),
    ]
    for chained in (
        exc_info.value.__cause__,
        exc_info.value.__context__,
    ):
        if chained is not None:
            parts.extend((str(chained), repr(chained)))
    return "\n".join(parts)


_RESPONSE_SECRET_MARKERS = (
    "http://USER_SECRET:PASSWORD_SECRET@proxy.invalid",
    "Authorization: Basic BASIC_SECRET",
    "Authorization: Bearer BEARER_SECRET",
    "token=TOKEN_SECRET",
    "BODY_SECRET",
    "/Users/private/SECRET_PATH/file",
    "RAW_EXCEPTION_MESSAGE_MARKER",
    "INVALID_UTF8_BYTES_MARKER",
)
_RESPONSE_SECRET_BLOB = " | ".join(_RESPONSE_SECRET_MARKERS)


def _assert_safe_fotmob_failure(exc_info, caplog, expected_type_name):
    combined = _exception_and_log_text(exc_info, caplog)
    for forbidden in _RESPONSE_SECRET_MARKERS:
        assert forbidden not in combined
    assert type(exc_info.value).__name__ == expected_type_name
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    return combined


def _external_decode_exception(kind):
    if kind == "value_error":
        return ValueError(_RESPONSE_SECRET_BLOB)
    if kind == "json_decode_error":
        return json.JSONDecodeError(
            _RESPONSE_SECRET_BLOB,
            _RESPONSE_SECRET_BLOB,
            0,
        )
    if kind == "unicode_decode_error":
        return UnicodeDecodeError(
            "utf-8",
            b"\xffINVALID_UTF8_BYTES_MARKER",
            0,
            1,
            _RESPONSE_SECRET_BLOB,
        )

    class ExternalResponseDecodeError(Exception):
        pass

    return ExternalResponseDecodeError(_RESPONSE_SECRET_BLOB)


def test_fotmob_transport_error_redacts_raw_exception_and_chain(
    monkeypatch, caplog,
):
    from backend import fotmob_client as module

    secret_url = (
        "http://PROXY_USER_UNIQUE:PROXY_PASS_UNIQUE@proxy.example:8080"
    )
    raw_message = f"proxy failed {secret_url}"

    def fail_request(*args, **kwargs):
        raise RuntimeError(raw_message)

    monkeypatch.setattr(module.cffi_requests, "get", fail_request)
    client = module.FotMobClient(
        proxy="", max_retries=1, retry_delay=0,
    )
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            client._get("https://www.fotmob.com/api/data/teams?id=8456")

    combined = _exception_and_log_text(exc_info, caplog)
    for forbidden in (
        "PROXY_USER_UNIQUE",
        "PROXY_PASS_UNIQUE",
        secret_url,
        raw_message,
    ):
        assert forbidden not in combined
    assert type(exc_info.value).__name__ == "FotMobTransportError"
    assert "RuntimeError" in combined
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_fotmob_proxy_error_redacts_all_external_auth_shapes(
    monkeypatch, caplog,
):
    from backend import fotmob_client as module

    class ProxyError(Exception):
        pass

    forbidden_markers = (
        "proxy=PROXY_CHAIN_USER:PROXY_CHAIN_PASS@proxy.example",
        "Authorization: Basic QVVUSF9TRUNSRVQ=",
        "encoded%40user:encoded%3Apass@proxy.example",
    )

    def fail_request(*args, **kwargs):
        raise ProxyError(" | ".join(forbidden_markers))

    monkeypatch.setattr(module.cffi_requests, "get", fail_request)
    client = module.FotMobClient(
        proxy="", max_retries=1, retry_delay=0,
    )
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            client._get("https://www.fotmob.com/api/data/teams?id=8456")

    combined = _exception_and_log_text(exc_info, caplog)
    for forbidden in forbidden_markers:
        assert forbidden not in combined
    assert type(exc_info.value).__name__ == "FotMobTransportError"
    assert "ProxyError" in combined
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize("status_code", [403, 500])
def test_fotmob_http_error_never_reads_or_exposes_response_body(
    monkeypatch, caplog, status_code,
):
    from backend import fotmob_client as module

    body_secret = f"HTTP_BODY_SECRET_{status_code}"

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        @property
        def text(self):
            return body_secret

    monkeypatch.setattr(
        module.cffi_requests,
        "get",
        lambda *args, **kwargs: FakeResponse(),
    )
    client = module.FotMobClient(
        proxy="", max_retries=1, retry_delay=0,
    )
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            client._get("https://www.fotmob.com/api/data/teams?id=8456")

    combined = _exception_and_log_text(exc_info, caplog)
    assert body_secret not in combined
    assert type(exc_info.value).__name__ == "FotMobHTTPError"
    assert f"HTTP status {status_code}" in combined
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_fotmob_status_code_accessor_failure_retries_inside_transport_boundary(
    monkeypatch, caplog,
):
    from backend import fotmob_client as module

    calls = []

    class RaisingStatusResponse:
        @property
        def status_code(self):
            raise RuntimeError(_RESPONSE_SECRET_BLOB)

    def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        return RaisingStatusResponse()

    monkeypatch.setattr(module.cffi_requests, "get", fake_request)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(module.random, "uniform", lambda *_: 0)
    client = module.FotMobClient(
        proxy="", max_retries=3, retry_delay=0,
    )

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            client._get("https://www.fotmob.com/api/data/teams?id=8456")

    assert len(calls) == 3
    combined = _assert_safe_fotmob_failure(
        exc_info, caplog, "FotMobTransportError",
    )
    assert "RuntimeError" in combined


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("check_ip", ()),
        ("daily_matches", ("20260725",)),
        ("league_matches", (47,)),
        ("team_data", (8456,)),
        (
            "fetch_stat_leaderboard",
            ("https://data.fotmob.com/safe-stat-endpoint",),
        ),
    ],
)
@pytest.mark.parametrize(
    "exception_kind",
    [
        "value_error",
        "json_decode_error",
        "unicode_decode_error",
        "custom_error",
    ],
)
def test_public_json_methods_sanitize_http_200_decode_failures(
    monkeypatch, caplog, method_name, args, exception_kind,
):
    from backend import fotmob_client as module

    class RaisingJsonResponse:
        status_code = 200

        def json(self):
            raise _external_decode_exception(exception_kind)

    client = module.FotMobClient(proxy="")
    monkeypatch.setattr(client, "_get", lambda *a, **k: RaisingJsonResponse())

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            getattr(client, method_name)(*args)

    combined = _assert_safe_fotmob_failure(
        exc_info, caplog, "FotMobDecodeError",
    )
    assert type(_external_decode_exception(exception_kind)).__name__ in combined


def test_real_curl_cffi_response_body_is_sanitized_on_json_decode_failure(
    monkeypatch, caplog, capsys,
):
    from backend import fotmob_client as module

    response = module.cffi_requests.Response()
    response.status_code = 200
    response.content = (
        b"\xffINVALID_UTF8_BYTES_MARKER "
        b'{"payload":"BODY_SECRET","token":"TOKEN_SECRET", malformed}'
    )
    client = module.FotMobClient(proxy="")
    monkeypatch.setattr(client, "_get", lambda *a, **k: response)

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(module.FotMobDecodeError) as exc_info:
            client.team_data(8456)

    combined = _assert_safe_fotmob_failure(
        exc_info, caplog, "FotMobDecodeError",
    )
    captured = capsys.readouterr()
    all_surfaces = "\n".join((
        combined,
        repr(exc_info.value.args),
        captured.out,
        captured.err,
    ))
    for forbidden in _RESPONSE_SECRET_MARKERS:
        assert forbidden not in all_surfaces

    # curl_cffi/Python versions may reject these bytes during UTF-8 decoding
    # or later during JSON decoding. That external implementation detail is
    # not the contract: both paths must become the same safe project error.
    message_prefix = "FotMob response decode error team_data "
    assert str(exc_info.value).startswith(message_prefix)
    external_exception_class = str(exc_info.value).removeprefix(
        message_prefix,
    )
    assert external_exception_class.isascii()
    assert external_exception_class.isidentifier()
    assert "operation=team_data" in caplog.text
    assert f"type={external_exception_class}" in caplog.text


def test_match_details_sanitizes_response_text_accessor_failure(
    monkeypatch, caplog,
):
    from backend import fotmob_client as module

    class RaisingTextResponse:
        status_code = 200

        @property
        def text(self):
            raise ValueError(_RESPONSE_SECRET_BLOB)

    client = module.FotMobClient(proxy="")
    monkeypatch.setattr(client, "_get", lambda *a, **k: RaisingTextResponse())

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            client.match_details("12345")

    combined = _assert_safe_fotmob_failure(
        exc_info, caplog, "FotMobDecodeError",
    )
    assert "ValueError" in combined


def test_match_details_sanitizes_invalid_next_data_json(
    monkeypatch, caplog,
):
    from backend import fotmob_client as module

    class InvalidNextDataResponse:
        status_code = 200
        text = (
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"secret":"' + _RESPONSE_SECRET_BLOB + '", malformed}'
            "</script>"
        )

    client = module.FotMobClient(proxy="")
    monkeypatch.setattr(client, "_get", lambda *a, **k: InvalidNextDataResponse())

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        with pytest.raises(Exception) as exc_info:
            client.match_details("12345")

    combined = _assert_safe_fotmob_failure(
        exc_info, caplog, "FotMobDecodeError",
    )
    assert "JSONDecodeError" in combined


def test_parse_season_player_stats_warning_omits_failed_definition_and_error(
    monkeypatch, caplog,
):
    from backend import fotmob_client as module

    secret_stat_name = "STAT_NAME_" + _RESPONSE_SECRET_BLOB
    secret_url = "https://data.fotmob.invalid/" + _RESPONSE_SECRET_BLOB
    league_data = {
        "stats": {
            "players": [
                {"name": secret_stat_name, "fetchAllUrl": secret_url},
                {
                    "name": "goals",
                    "fetchAllUrl": "https://data.fotmob.invalid/safe",
                },
            ]
        }
    }
    safe_payload = {
        "TopLists": [{
            "StatList": [{
                "ParticipantName": "Safe Player",
                "ParticiantId": 7,
                "TeamId": 8,
                "TeamName": "Safe Team",
                "StatValue": 9,
                "Rank": 1,
            }]
        }]
    }
    client = module.FotMobClient(proxy="")

    def fake_fetch(url):
        if url == secret_url:
            raise RuntimeError(_RESPONSE_SECRET_BLOB)
        return safe_payload

    monkeypatch.setattr(client, "fetch_stat_leaderboard", fake_fetch)
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        records = client.parse_season_player_stats(
            league_data, 47, "2024/2025",
        )

    assert len(records) == 1
    assert records[0]["Player_Name"] == "Safe Player"
    for forbidden in _RESPONSE_SECRET_MARKERS:
        assert forbidden not in caplog.text
    assert secret_stat_name not in caplog.text
    assert secret_url not in caplog.text
    assert "RuntimeError" in caplog.text


def test_team_data_url_construction_no_season(monkeypatch):
    sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))
    from fotmob_client import FotMobClient

    captured = {}

    class FakeResp:
        def json(self):
            return {"fixtures": {}}

    def fake_get(self, url, headers=None):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(FotMobClient, "_get", fake_get)
    client = FotMobClient(proxy="")
    result = client.team_data(8456)
    assert captured["url"] == "https://www.fotmob.com/api/data/teams?id=8456"
    assert result == {"fixtures": {}}


def test_team_data_url_construction_with_season(monkeypatch):
    sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))
    from fotmob_client import FotMobClient

    captured = {}

    class FakeResp:
        def json(self):
            return {"fixtures": {}}

    def fake_get(self, url, headers=None):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(FotMobClient, "_get", fake_get)
    client = FotMobClient(proxy="")
    client.team_data(8456, season="2024/2025")
    assert captured["url"] == "https://www.fotmob.com/api/data/teams?id=8456&season=2024/2025"


# ── 端到端:解析真实(裁剪)fixture ─────────────────────────────────────────

def test_end_to_end_parses_real_trimmed_fixture():
    with open(FIXTURE_PATH) as f:
        raw = json.load(f)
    records = parse_team_schedule_response(
        raw, TEAM_ID, "2024/2025", "offline_fixture:test", "2026-07-23T00:00:00Z"
    )
    assert len(records) == 12

    by_class = {}
    for r in records:
        by_class.setdefault(r["competition_class"], []).append(r)

    assert len(by_class.get("league", [])) == 8  # 6 已完赛 + 2 未开赛
    assert len(by_class.get("domestic_cup", [])) == 2  # FA Cup ×2
    assert len(by_class.get("friendly", [])) == 1
    assert len(by_class.get("super_cup", [])) == 1
    assert "continental" not in by_class  # 真实数据里确实没有,如实体现

    precisions = {r["kickoff_precision"] for r in records}
    assert "exact" in precisions
    assert "unknown" in precisions  # matchDateTbd=true 的那场

    rest = compute_rest_hours(records)
    examples = find_cross_comp_rest_examples(rest)
    assert len(examples) >= 1  # 真实的足总杯插入英超样例应能复现


# ── CLI:离线模式 ──────────────────────────────────────────────────────────

def test_cli_rejects_without_live_or_offline_fixture(tmp_path, capsys):
    code = run_cli(["--team-id", "8456", "--season", "2024/2025",
                     "--output-dir", str(tmp_path)])
    assert code == 2


def test_cli_rejects_both_live_and_offline_fixture(tmp_path):
    code = run_cli(["--team-id", "8456", "--season", "2024/2025", "--live",
                     "--offline-fixture", FIXTURE_PATH, "--output-dir", str(tmp_path)])
    assert code == 2


def test_cli_offline_mode_end_to_end_and_idempotent(tmp_path, capsys):
    out_dir = str(tmp_path / "run1")
    code = run_cli(["--team-id", "8456", "--season", "2024/2025",
                     "--offline-fixture", FIXTURE_PATH, "--output-dir", out_dir])
    assert code == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["total_records"] == 12
    assert summary["calendar_write"] == {"inserted": 12, "skipped": 0}

    db_path = summary["db_path"]
    conn = sqlite3.connect(db_path)
    n1 = conn.execute("SELECT COUNT(*) FROM pilot_match_calendar").fetchone()[0]
    conn.close()
    assert n1 == 12

    # 第二次跑同一个 output-dir(同一个 db)——必须幂等,行数不增加
    code2 = run_cli(["--team-id", "8456", "--season", "2024/2025",
                      "--offline-fixture", FIXTURE_PATH, "--output-dir", out_dir])
    assert code2 == 0
    captured2 = capsys.readouterr()
    summary2 = json.loads(captured2.out)
    assert summary2["calendar_write"] == {"inserted": 0, "skipped": 12}

    conn = sqlite3.connect(db_path)
    n2 = conn.execute("SELECT COUNT(*) FROM pilot_match_calendar").fetchone()[0]
    conn.close()
    assert n2 == 12  # 未增加

    assert "THORDATA_PROXY" not in captured.out
    assert "THORDATA_PROXY" not in captured2.out
