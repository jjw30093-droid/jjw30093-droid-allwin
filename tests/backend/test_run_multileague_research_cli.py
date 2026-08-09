"""run_multileague_research CLI 端到端契约测试(2026-08-07 窄幅收口)。

用最小三库夹具(6 假赛季、单联赛、少量比赛)跑通整条编排,验证:
- 新空 output-dir 从临时三库完整运行;
- 相同输入重复运行 artifact hash 一致;
- 缺表 fail closed;
- 中途失败不留伪装完成的 manifest;
- 输入库前后 hash/size 不变(只读契约);
- manifest 使用的 dataset_hash 与 pit_dataset.build_dataset 直接产出一致
  (即 lineage 修复已生效的那个 hash 算法路径,不是旧值)。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from backend.cli.run_multileague_research import ResearchInputError, main, run


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_core(path: Path, n_per_season: int = 24) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE dim_match (Match_ID INTEGER PRIMARY KEY, League_ID INTEGER,
          Season TEXT, Date TEXT, kickoff_at_utc TEXT, kickoff_precision TEXT,
          Home_Team_ID INTEGER, Away_Team_ID INTEGER, home_score INTEGER,
          away_score INTEGER, Match_Round TEXT, status TEXT);
        CREATE TABLE fact_team_match_stats (Match_ID INTEGER, Team_ID INTEGER,
          Period TEXT, extra_json TEXT);
        CREATE TABLE fact_league_table (League_ID INTEGER, Season TEXT);
        CREATE TABLE fact_season_player_stats (League_ID INTEGER, Season TEXT);
        CREATE TABLE fact_shotmap (Match_ID INTEGER);
        CREATE TABLE fact_player_match_stats (Match_ID INTEGER);
        CREATE TABLE fact_match_events (Match_ID INTEGER);
        CREATE TABLE fact_match_lineup (Match_ID INTEGER);
        CREATE TABLE int_match_features (Match_ID INTEGER);
        CREATE TABLE gold_wdl_predictions (match_id INTEGER);
    """)
    seasons = ["2020/2021", "2021/2022", "2022/2023", "2023/2024", "2024/2025", "2025/2026"]
    mid = 1
    for si, season in enumerate(seasons):
        year = 2020 + si
        for i in range(n_per_season):
            home, away = 100 + (i % 6), 200 + (i % 5)
            ko = f"{year}-08-{(i % 27) + 1:02d}T15:00:00Z"
            conn.execute(
                "INSERT INTO dim_match VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, 47, season, ko[:10], ko, "exact", home, away,
                 i % 3, (i + 1) % 3, str(i % 10 + 1), "Finish"))
            for tid in (home, away):
                conn.execute(
                    "INSERT INTO fact_team_match_stats VALUES (?,?,?,?)",
                    (mid, tid, "All",
                     json.dumps({"expected_goals": 1.0 + (mid % 5) * 0.1, "total_shots": 10,
                                "ShotsOnTarget": 4, "BallPossesion": 50})))
            mid += 1
    conn.commit()
    conn.close()


def _build_odds(path: Path, core_path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE dim_match_xref (provider TEXT, provider_match_id TEXT,
          fotmob_match_id INTEGER, review_status TEXT);
        CREATE TABLE bronze_ng_odds_snap (id INTEGER PRIMARY KEY, provider_match_id TEXT,
          market TEXT, market_phase TEXT, company_id TEXT, company_name TEXT,
          payload_json TEXT, observed_at TEXT);
        CREATE TABLE bronze_legacy_odds_summary (fotmob_match_id INTEGER, market TEXT,
          period TEXT, source TEXT, provider TEXT, home_or_over REAL, draw REAL,
          away_or_under REAL);
    """)
    core = sqlite3.connect(str(core_path))
    matches = core.execute("SELECT Match_ID, kickoff_at_utc FROM dim_match").fetchall()
    core.close()
    for mid, ko in matches:
        pmid = f"T{mid}"
        conn.execute("INSERT INTO dim_match_xref VALUES ('nowgoal', ?, ?, 'auto_ok')", (pmid, mid))
        obs = ko[:11] + f"{int(ko[11:13]) - 2:02d}" + ko[13:]  # kickoff 前两小时
        conn.execute(
            "INSERT INTO bronze_ng_odds_snap (provider_match_id, market, market_phase, "
            "company_id, company_name, payload_json, observed_at) VALUES (?,?,?,?,?,?,?)",
            (pmid, "1x2", "pre_match", "177", "pinnacle",
             json.dumps({"home": 2.0, "draw": 3.4, "away": 3.6}), obs))
        conn.execute(
            "INSERT INTO bronze_legacy_odds_summary VALUES (?,?,?,?,?,?,?,?)",
            (mid, "1x2", "latest", "asset_a_json", "Bet365", 2.1, 3.3, 3.5))
    conn.commit()
    conn.close()


def _build_platform(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE prediction_snapshots (match_id INTEGER, status TEXT,
          is_official INTEGER, locked_at TEXT);
        CREATE TABLE prediction_evaluations (id INTEGER);
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def three_dbs(tmp_path: Path) -> tuple[Path, Path, Path]:
    core = tmp_path / "allwin.db"
    odds = tmp_path / "odds.db"
    platform = tmp_path / "platform.db"
    _build_core(core)
    _build_odds(odds, core)
    _build_platform(platform)
    return core, odds, platform


class TestEndToEnd:
    def test_fresh_output_dir_runs_to_completion(self, three_dbs, tmp_path):
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        manifest = run(str(core), str(odds), str(platform), str(out))
        assert (out / "manifest.json").exists()
        for name in ("coverage.json", "coverage.md", "dataset_manifest.json",
                     "dataset_rows.json", "market_baseline_report.json",
                     "market_probs.json", "fold_metrics.json",
                     "paired_market_metrics.json", "feature_ablation.json"):
            assert (out / name).exists(), f"missing {name}"
        fm = json.loads((out / "fold_metrics.json").read_text())
        assert len(fm) == 5
        assert manifest["dataset_hash"]

    def test_no_generation_metadata_in_artifacts(self, three_dbs, tmp_path):
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        run(str(core), str(odds), str(platform), str(out))
        for name in ("coverage.json", "dataset_manifest.json", "market_baseline_report.json",
                     "fold_metrics.json", "paired_market_metrics.json", "feature_ablation.json"):
            text = (out / name).read_text()
            assert str(tmp_path) not in text, f"{name} 泄漏了绝对路径"
            assert "generated_at" not in text and "hostname" not in text


class TestDeterminism:
    def test_repeat_run_same_output_dir_identical_hashes(self, three_dbs, tmp_path):
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        m1 = run(str(core), str(odds), str(platform), str(out))
        m2 = run(str(core), str(odds), str(platform), str(out))
        assert m1["manifest_hash"] == m2["manifest_hash"]
        assert m1["artifact_hashes"] == m2["artifact_hashes"]

    def test_different_absolute_paths_same_business_hash(self, three_dbs, tmp_path):
        """把同一份数据复制到另一条绝对路径下的 output-dir,业务哈希必须相同
        (manifest 本身不含路径,artifact 内容也不含路径)。"""
        core, odds, platform = three_dbs
        out_a = tmp_path / "results_a"
        out_b = tmp_path / "deeply" / "nested" / "results_b"
        m1 = run(str(core), str(odds), str(platform), str(out_a))
        m2 = run(str(core), str(odds), str(platform), str(out_b))
        assert m1["artifact_hashes"] == m2["artifact_hashes"]
        assert m1["dataset_hash"] == m2["dataset_hash"]

    def test_manifest_uses_lineage_fixed_dataset_hash(self, three_dbs, tmp_path):
        """manifest 的 dataset_hash 必须等于 pit_dataset.build_dataset 直接产出
        的 hash(即 lineage 修复后的算法路径),不是某个旧的硬编码值。"""
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from backend.models.research.pit_dataset import build_dataset

        core, odds, platform = three_dbs
        out = tmp_path / "results"
        manifest = run(str(core), str(odds), str(platform), str(out))

        conn = sqlite3.connect(f"file:{core}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        direct = build_dataset(conn, leagues=(47,))
        conn.close()
        assert manifest["dataset_hash"] == direct["dataset_hash"]


class TestFailClosed:
    def test_missing_table_fails_closed_no_output(self, three_dbs, tmp_path):
        core, odds, platform = three_dbs
        conn = sqlite3.connect(str(odds))
        conn.execute("DROP TABLE bronze_legacy_odds_summary")
        conn.commit()
        conn.close()
        out = tmp_path / "results"
        with pytest.raises(ResearchInputError):
            run(str(core), str(odds), str(platform), str(out))
        # fail closed:不产出任何文件(甚至不建目录里留半成品)
        assert not out.exists() or list(out.iterdir()) == []

    def test_cli_main_returns_nonzero_on_missing_table(self, three_dbs, tmp_path, capsys):
        core, odds, platform = three_dbs
        conn = sqlite3.connect(str(core))
        conn.execute("DROP TABLE fact_team_match_stats")
        conn.commit()
        conn.close()
        out = tmp_path / "results"
        rc = main(["--core", str(core), "--odds", str(odds),
                  "--platform", str(platform), "--output-dir", str(out)])
        assert rc != 0
        err = capsys.readouterr().err
        assert "fail-closed" in err

    def test_midrun_failure_leaves_no_fake_complete_manifest(self, three_dbs, tmp_path, monkeypatch):
        """在写完部分产物后于 run_full_study 阶段注入异常:manifest.json 绝不
        出现,即使前面的 coverage/dataset/market 产物已经落盘。"""
        import backend.cli.run_multileague_research as cli_mod

        def _boom(*a, **k):
            raise RuntimeError("injected mid-run failure")

        monkeypatch.setattr(
            "backend.models.research.run_research.run_full_study", _boom)
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        with pytest.raises(RuntimeError, match="injected mid-run failure"):
            run(str(core), str(odds), str(platform), str(out))
        assert (out / "coverage.json").exists()          # 前面阶段已落盘(诚实)
        assert not (out / "manifest.json").exists()       # 但绝不伪装"已完成"


class TestReadOnlyContract:
    def test_input_databases_unchanged_after_run(self, three_dbs, tmp_path):
        core, odds, platform = three_dbs
        before = {p: (_file_hash(p), p.stat().st_size) for p in (core, odds, platform)}
        run(str(core), str(odds), str(platform), str(tmp_path / "results"))
        after = {p: (_file_hash(p), p.stat().st_size) for p in (core, odds, platform)}
        assert before == after


class TestExistingOutputRerunFailure:
    """真 P1(用户 2026-08-07 复核确认):对已有一次成功产物的 output-dir
    重跑,若新一轮在写入部分 artifact 后失败,旧 manifest.json 此前会
    继续留在目录里——磁盘状态变成"旧成功 manifest + 新一轮部分 artifact +
    本轮失败",manifest 不再如实描述目录内容,违反"manifest 是完整成功
    信号"的契约。

    安全契约选择 B(最小 fail-closed,详见 run_multileague_research.py
    docstring):完成输入库校验后、修改任何 artifact 前,先原子撤销旧
    manifest;新 manifest 仍只在全部成功后最后写入。因此重跑一旦真正
    开始覆写 artifact,manifest.json 在成功之前必然缺席——不会出现
    "旧 manifest + 新部分 artifact"的混合态。
    """

    def test_early_stage_failure_on_rerun_leaves_no_manifest(self, three_dbs, tmp_path, monkeypatch):
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        first = run(str(core), str(odds), str(platform), str(out))
        assert (out / "manifest.json").exists()

        def _boom(*a, **k):
            raise RuntimeError("injected early-stage rerun failure")

        monkeypatch.setattr("backend.models.research.run_research.run_full_study", _boom)
        with pytest.raises(RuntimeError, match="injected early-stage rerun failure"):
            run(str(core), str(odds), str(platform), str(out))

        # 核心不变量:manifest 绝不能继续存在并把混合目录说成"已完成"
        assert not (out / "manifest.json").exists(), (
            "旧 manifest 在新一轮失败后仍然存在——把部分覆写的目录伪装成已完成"
        )
        # 旧 manifest 描述的 dataset_hash 不应该继续被信任
        assert first["dataset_hash"]  # 仅确认第一次跑确实产出过有效结果

    def test_late_stage_failure_after_fold_metrics_written_leaves_no_manifest(
        self, three_dbs, tmp_path, monkeypatch
    ):
        """比上一条更晚的故障点:fold_metrics.json 已经写盘之后、
        paired_market_metrics.json 之前失败——同一契约仍必须成立。"""
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        run(str(core), str(odds), str(platform), str(out))
        assert (out / "manifest.json").exists()

        import backend.cli.run_multileague_research as cli_mod

        real_write = cli_mod._atomic_write_json
        calls: list[Path] = []

        def _fail_after_fold_metrics(path, obj):
            calls.append(Path(path))
            if Path(path).name == "fold_metrics.json":
                real_write(path, obj)  # fold_metrics.json 真实落盘
                raise RuntimeError("injected late-stage rerun failure")
            return real_write(path, obj)

        monkeypatch.setattr(cli_mod, "_atomic_write_json", _fail_after_fold_metrics)
        with pytest.raises(RuntimeError, match="injected late-stage rerun failure"):
            run(str(core), str(odds), str(platform), str(out))

        assert any(p.name == "fold_metrics.json" for p in calls), "故障注入点未被覆盖到(测试本身失效)"
        assert not (out / "manifest.json").exists(), (
            "fold_metrics.json 已覆写、paired_market_metrics.json 之前失败时,"
            "旧 manifest 仍不得继续存在"
        )

    def test_repeated_successful_reruns_still_produce_manifest(self, three_dbs, tmp_path):
        """反向确认:修复不能变成"永远不写 manifest"——正常连续成功的重跑
        必须每次都拿到新 manifest,且业务内容不变(与既有确定性测试互补)。"""
        core, odds, platform = three_dbs
        out = tmp_path / "results"
        m1 = run(str(core), str(odds), str(platform), str(out))
        m2 = run(str(core), str(odds), str(platform), str(out))
        assert (out / "manifest.json").exists()
        assert m1["artifact_hashes"] == m2["artifact_hashes"]
