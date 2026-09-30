"""模拟器参数每日导出:到期判断、argv 接线、发布校验(docs/simulator-launch-plan.md §3.1)。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.cli import simulator_params_export as spe
from backend.worker import runner

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    sys.path.insert(0, str(ROOT / "scripts" / "simulator"))
    sys.path.insert(0, str(ROOT / "research" / "ah_signals"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "simulator" / f"{name}.py")
    if name in sys.modules:
        return sys.modules[name]
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass 需要模块已登记在 sys.modules
    spec.loader.exec_module(mod)
    return mod


def utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


OK = {"status": "succeeded", "finished_at": "2026-09-20T22:10:00Z"}


def test_calibrated_leagues_match_params_core():
    assert spe.CALIBRATED_LEAGUES == _load_script("params_core").CALIBRATED_LEAGUES


class TestDecide:
    def test_today_already_published(self):
        assert spe.decide(utc("2026-09-21T00:00:00Z"), True, OK, [])["reason"] == "today_published"

    def test_before_window(self):
        # 北京 04:00 < 04:30
        assert spe.decide(utc("2026-09-20T20:00:00Z"), False, OK, [])["reason"] == "before_window"

    def test_ready_after_collection(self):
        d = spe.decide(utc("2026-09-20T22:35:00Z"), False, OK, [])  # 北京 06:35
        assert d["due"] and d["trigger"] == "ready"

    def test_waits_for_unresolved_match(self):
        d = spe.decide(utc("2026-09-20T22:35:00Z"), False, OK, [5103641])
        assert not d["due"] and d["reason"] == "waiting_for_collection" and d["unresolved_match_ids"] == [5103641]

    def test_waits_when_last_collection_failed(self):
        d = spe.decide(utc("2026-09-20T22:35:00Z"), False, {"status": "failed"}, [])
        assert not d["due"]

    def test_deadline_exports_anyway(self):
        d = spe.decide(utc("2026-09-21T04:05:00Z"), False, OK, [5103641])  # 北京 12:05
        assert d["due"] and d["trigger"] == "deadline"

    def test_published_name_uses_beijing_date(self):
        assert spe.published_name(utc("2026-09-20T17:00:00Z")) == "simulator_params_20260921.json"


def test_worker_argv_wired():
    """CLAUDE.md §6.3:新增开关必须断言 worker argv 带上它。"""
    spec = runner.REGISTRY["simulator_params_export"]
    assert "backend.cli.simulator_params_export" in spec["argv"]
    assert "--due" in spec["argv"]
    assert "simulator_params_export" in runner.NON_CHAIN_JOBS


def test_export_argv_publishes(monkeypatch, tmp_path):
    seen = {}

    class R:
        returncode = 0

    def fake_run(argv, check):
        seen["argv"] = argv
        return R()

    monkeypatch.setattr(spe.subprocess, "run", fake_run)
    assert spe.run_export(tmp_path, 7, {"trigger": "ready"}) == 0
    argv = seen["argv"]
    assert str(spe.EXPORT_SCRIPT) in argv
    assert "--publish" in argv
    assert argv[argv.index("--keep") + 1] == "7"
    assert json.loads(argv[argv.index("--trigger-json") + 1]) == {"trigger": "ready"}


def test_main_not_due_does_not_export(monkeypatch, tmp_path):
    monkeypatch.setattr(spe, "last_collection_run", lambda: OK)
    monkeypatch.setattr(spe, "unresolved_matches", lambda now_iso: [1])
    monkeypatch.setattr(spe, "decide", lambda *a: {"due": False, "reason": "waiting_for_collection"})
    called = []
    monkeypatch.setattr(spe, "run_export", lambda *a: called.append(a) or 0)
    assert spe.main(["--due", "--out-dir", str(tmp_path)]) == 0
    assert called == []


# ------------------------------------------------------------------ 发布
def _out(n_teams: int = 20, leagues=(47, 87, 55, 54, 53)) -> dict:
    teams = {}
    for lid in leagues:
        for i in range(n_teams):
            teams[f"{lid}{i:03d}"] = {"league_id": lid, "last_lineup": {"formation": "4-3-3"}}
    return {
        "meta": {"effective_version": "v0.3"},
        "calibration": {"market_w": 1},
        "leagues": {str(l): {} for l in leagues},
        "teams": teams,
        "pad": "x" * 600_000,
    }


@pytest.fixture(scope="module")
def ep():
    return _load_script("export_params")


def test_validate_rejects_wrong_league_set(ep):
    errs = ep.validate_export(_out(leagues=(47, 87)), 700_000)
    assert any("联赛集合" in e for e in errs)
    assert ep.validate_export(_out(), 700_000) == []


def test_publish_writes_current_link_and_prunes(ep, tmp_path):
    for day in range(1, 10):
        (tmp_path / f"simulator_params_202609{day:02d}.json").write_text("{}")
    final = ep.publish(_out(), tmp_path, keep=3, now=utc("2026-09-20T23:00:00Z"))
    assert final.name == "simulator_params_20260921.json"
    link = tmp_path / "current.json"
    assert link.is_symlink() and os.readlink(link) == final.name
    assert json.loads(link.read_text())["meta"]["effective_version"] == "v0.3"
    kept = sorted(p.name for p in tmp_path.glob("simulator_params_*.json"))
    assert kept == ["simulator_params_20260908.json", "simulator_params_20260909.json", "simulator_params_20260921.json"]
    assert not list(tmp_path.glob(".tmp-*"))


def test_failed_validation_keeps_current(ep, tmp_path):
    good = ep.publish(_out(), tmp_path, keep=7, now=utc("2026-09-20T23:00:00Z"))
    with pytest.raises(SystemExit):
        ep.publish(_out(n_teams=5), tmp_path, keep=7, now=utc("2026-09-21T23:00:00Z"))
    assert os.readlink(tmp_path / "current.json") == good.name
    assert not (tmp_path / "simulator_params_20260922.json").exists()
    assert not list(tmp_path.glob(".tmp-*"))


def test_attach_crests_null_when_missing_or_media_broken(ep):
    from backend.media.team_crests import TeamCrestError

    def resolver(provider, team_id):
        assert provider == "fotmob"
        if team_id == 1:
            return "/api/v1/media/team-crests/fotmob/1.png?v=abcdefabcdef"
        if team_id == 3:
            raise TeamCrestError("unsafe media directory")
        return None

    teams = {"1": {}, "2": {}, "3": {}}
    assert ep.attach_crests(teams, resolver) == 1
    assert teams["1"]["crest_url"].endswith("1.png?v=abcdefabcdef")
    assert teams["2"]["crest_url"] is None
    assert teams["3"]["crest_url"] is None


def test_attach_crests_real_resolver_uses_media_dir(ep, tmp_path, monkeypatch):
    # 空媒体目录:真实解析器返回 None,不抛错(导出不因队徽缺失失败)
    monkeypatch.setenv("ALLWIN_MEDIA_DIR", str(tmp_path))
    teams = {"9825": {}}
    assert ep.attach_crests(teams) == 0
    assert teams["9825"]["crest_url"] is None


# ------------------------------------------------------------------ G16 新鲜度质量门
def test_gate_simulator_params_stale(tmp_path):
    from backend.cli.pipeline_gates import _gate_simulator_params_stale

    assert _gate_simulator_params_stale(None, "2026-09-30T00:00:00Z")["skipped"] is True
    miss = _gate_simulator_params_stale(str(tmp_path), "2026-09-30T00:00:00Z")
    assert miss["level"] == "WARNING" and miss["detail"] == "unreadable"
    (tmp_path / "p.json").write_text(json.dumps({"meta": {"generated_at": "2026-09-29T00:00:00Z"}}))
    os.symlink("p.json", tmp_path / "current.json")
    assert _gate_simulator_params_stale(str(tmp_path), "2026-09-30T06:00:00Z")["level"] == "OK"
    stale = _gate_simulator_params_stale(str(tmp_path), "2026-09-30T13:00:00Z")
    assert stale["level"] == "WARNING" and stale["age_hours"] == 37.0
