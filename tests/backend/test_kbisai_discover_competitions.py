"""kbisai_discover_competitions CLI 回归(本轮任务 §6)。

find_by_name 是纯函数(树搜索),用真实的 comp/category 解密结果(仓库里已有,
2026-08-04 真实抓取)做数据,断言英超/瑞典超能唯一定位、且同名重复(如瑞典超/
瑞典超甲/瑞典甲这种命名接近但id不同)不会被静默合并成一个结果。

run() 的网络部分用 monkeypatch 替身(同 test_kbisai_live_scores.py::TestCliArtifacts
的写法),只验证 artifact 落盘与 summary 结构,不重复测真实网络请求。
"""

import json
import os
import stat
from pathlib import Path

import pytest

from backend.cli import kbisai_discover_competitions as cli

FIXTURE_TREE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "kbisai", "comp-category-tree.json",
)


@pytest.fixture(scope="module")
def real_tree():
    with open(FIXTURE_TREE_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestFindByName:
    def test_epl_resolves_to_single_id_across_multiple_paths(self, real_tree):
        hits = cli.find_by_name(real_tree, "英超")
        assert len(hits) >= 2   # 出现在"热门"和"欧洲>英格兰"等多个分类下
        assert {h["id"] for h in hits} == {"82"}   # 但全部指向同一个 id,不是歧义

    def test_sweden_top_flight_is_distinct_from_lower_tiers(self, real_tree):
        """瑞典超(Allsvenskan,真实 id=184)与瑞典超甲(id=185)、瑞典甲(id=186)
        是三个不同的联赛——find_by_name 精确匹配名字,不能把它们混在一起。"""
        top = cli.find_by_name(real_tree, "瑞典超")
        second = cli.find_by_name(real_tree, "瑞典超甲")
        third = cli.find_by_name(real_tree, "瑞典甲")
        assert {h["id"] for h in top} == {"184"}
        assert {h["id"] for h in second} == {"185"}
        assert {h["id"] for h in third} == {"186"}

    def test_unknown_name_returns_no_hits(self, real_tree):
        assert cli.find_by_name(real_tree, "不存在的联赛名字") == []


class TestRunArtifacts:
    def test_run_writes_private_auditable_artifacts(self, tmp_path: Path, monkeypatch, real_tree):
        monkeypatch.setattr(cli, "fetch_competition_category", lambda: real_tree)

        summary = cli.run(name="英超", output_dir=tmp_path)
        assert summary["status"] == "OK"
        assert summary["unique"] is True
        assert {h["id"] for h in summary["hits"]} == {"82"}

        run_dir = tmp_path / summary["run_id"]
        on_disk = json.loads((run_dir / "summary.json").read_text())
        assert on_disk == summary
        assert json.loads((run_dir / "comp-category.json").read_text()) == real_tree
        for path in run_dir.iterdir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
