"""按名字在 kbisai 联赛分类树里查找 competitionId,并写入可审计 artifact。

只做树搜索,不做任何"猜测最相似的名字"之类的模糊匹配——重名(如瑞典超/
瑞典超甲/瑞典甲这种命名接近但代表不同级别联赛的情况)必须由调用方用真实
赛程(kickoff + 队名)交叉核实,而不是从名字本身判断。已确认的 competitionId
以硬编码常量的形式进 collector 配置,不在每次采集时都重新搜索(见
backend/providers/kbisai_odds.py 模块 docstring)。

用法:
  .venv/bin/python -m backend.cli.kbisai_discover_competitions --name 英超
  .venv/bin/python -m backend.cli.kbisai_discover_competitions --name 瑞典
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from backend.providers.kbisai_live_scores import KbisaiLiveScoreError, safe_exception_class
from backend.providers.kbisai_odds import KbisaiOddsError, fetch_competition_category

DEFAULT_OUTPUT_DIR = Path("runtime/research/kbisai-competition-discovery")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _walk(node: Any, path: list[str]) -> list[tuple[list[str], Any, Any]]:
    out: list[tuple[list[str], Any, Any]] = []
    if isinstance(node, dict):
        out.append((path, node.get("id"), node.get("name")))
        for child in node.get("children") or []:
            out.extend(_walk(child, path + [f"{node.get('id')}:{node.get('name')}"]))
    elif isinstance(node, list):
        for child in node:
            out.extend(_walk(child, path))
    return out


def find_by_name(tree: dict, name: str) -> list[dict]:
    """精确名字匹配(不做模糊/子串以外的猜测),返回每个命中节点的 id + 完整路径。"""
    nodes = _walk(tree.get("categorys", []), [])
    hits = [
        {"id": str(node_id), "name": node_name, "path": path}
        for path, node_id, node_name in nodes
        if node_name == name
    ]
    return hits


def run(*, name: str, output_dir: Path) -> dict[str, Any]:
    tree = fetch_competition_category()
    hits = find_by_name(tree, name)

    run_id = uuid.uuid4().hex[:12]
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    os.chmod(run_dir, 0o700)
    _atomic_write(run_dir / "comp-category.json", _json_bytes(tree))

    summary = {
        "status": "OK",
        "run_id": run_id,
        "query_name": name,
        "hits": hits,
        "unique": len({h["id"] for h in hits}) == 1,
        "note": (
            "id 在多个路径下重复出现但完全一致 = 该联赛在树里的多个分类下都能找到,"
            "不是歧义;unique=false 且出现多个不同 id 时必须用真实赛程交叉核实,"
            "不能凭名字接近直接采用其中一个。"
        ),
        "artifact_files": ["comp-category.json", "summary.json"],
    }
    _atomic_write(run_dir / "summary.json", _json_bytes(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按名字查找 kbisai competitionId")
    parser.add_argument("--name", required=True, help="联赛中文名,精确匹配(如 英超/瑞典超)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(name=args.name, output_dir=args.output_dir)
    except (KbisaiLiveScoreError, KbisaiOddsError) as exc:
        print(json.dumps(
            {"status": "FAILED", "error": safe_exception_class(exc)},
            ensure_ascii=False, sort_keys=True,
        ))
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
