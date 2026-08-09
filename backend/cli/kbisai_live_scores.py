"""抓取一次春秋直播公开足球实时比分并保存可审计 artifact。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from backend.providers.kbisai_live_scores import (
    KbisaiLiveScoreError,
    assert_no_runtime_credentials,
    fetch_realtime_snapshot,
    listen_ws_updates,
    safe_exception_class,
)

DEFAULT_OUTPUT_DIR = Path("runtime/research/kbisai-live-scores")


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


def run(
    *,
    output_dir: Path,
    listen_seconds: float,
    max_events: int,
) -> dict[str, Any]:
    assert_no_runtime_credentials()
    snapshot = fetch_realtime_snapshot()
    run_id = snapshot.observed_at.replace(":", "").replace("-", "").replace(".", "")
    run_id = run_id.replace("Z", "") + "-" + uuid.uuid4().hex[:12]
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    os.chmod(run_dir, 0o700)

    _atomic_write(run_dir / "realtime-match-list.pb", snapshot.raw_bytes)
    _atomic_write(run_dir / "matches.json", _json_bytes(list(snapshot.matches)))

    ws_updates: tuple[dict[str, Any], ...] = ()
    ws_status = "NOT_REQUESTED"
    ws_error = None
    if listen_seconds > 0:
        try:
            ws_updates = listen_ws_updates(
                duration_seconds=listen_seconds,
                max_events=max_events,
            )
            ws_status = "OBSERVED"
        except KbisaiLiveScoreError as exc:
            # 可选增量通道失败不能抹掉已经验证并落盘的 REST 全量快照。
            ws_status = "UNAVAILABLE"
            ws_error = safe_exception_class(exc)
    _atomic_write(run_dir / "updates.json", _json_bytes(list(ws_updates)))

    summary = {
        "status": "OK",
        "run_id": run_id,
        **snapshot.public_summary(),
        "ws_observation_seconds": listen_seconds,
        "ws_status": ws_status,
        "ws_error": ws_error,
        "ws_update_count": len(ws_updates),
        "artifact_files": [
            "realtime-match-list.pb",
            "matches.json",
            "updates.json",
            "summary.json",
        ],
    }
    _atomic_write(run_dir / "summary.json", _json_bytes(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="抓取一次春秋直播匿名公开足球实时比分并写入 runtime artifact"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=0.0,
        help="可选 WebSocket 增量观察时长（0=只抓 REST 快照，最大 55 秒）",
    )
    parser.add_argument("--max-events", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            output_dir=args.output_dir,
            listen_seconds=args.listen_seconds,
            max_events=args.max_events,
        )
    except KbisaiLiveScoreError as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": safe_exception_class(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
