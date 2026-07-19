"""轻量 Worker(无 Celery):任务注册表 + job_runs 全生命周期 + 文件锁 + 有限重试/退避/超时。

设计(P0.11):
- 任务注册表 REGISTRY:name → dict(fn 或 subprocess spec, max_attempts, timeout_seconds,
  backoff_seconds, ...)。测试可用 register_job() 注入临时任务。
- run_job() 把一次执行的全生命周期写进 platform.db 的 job_runs
  (pending → running → succeeded/failed/skipped):
  * 幂等键 (job_name, idempotency_key) 已成功过 → 本次记 skipped(不重复执行,force=True 强制重跑);
  * 文件锁 data/locks/<job>.lock(os.O_CREAT|O_EXCL,写入 pid;pid 已死视为陈锁自动清理)防并发;
  * 有限重试 + 线性退避(backoff_seconds * attempt);
  * 超时:subprocess 用 subprocess.run(timeout=...);函数任务用线程 + 总时长兜底
    (超时后线程无法强杀,只能放弃等待并记 failed,故函数任务应自行保持短事务);
  * error_summary 截断存摘要(ERROR_SUMMARY_MAX);
  * input_count / output_count 由任务返回 dict 提供({"input_count": .., "output_count": .., "meta": {..}})。
- run_chain():按 DEFAULT_CHAIN 顺序执行;某步 failed 则后续全部记 skipped(依赖检查);
  --from <step> 支持从中间步骤重跑。
- 可选任务(nowgoal_snapshot / entity_resolution / analysis_bundle_build):依赖的模块
  尚未交付时记 skipped + reason,不算失败、不阻断链。

用法:
  python -m backend.worker.runner --list
  python -m backend.worker.runner --job silver_build
  python -m backend.worker.runner --job schedule_sync --key 2026-07-19
  python -m backend.worker.runner --chain
  python -m backend.worker.runner --chain --from silver_build
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from backend.db.connections import connect_rw, tx
from backend.db.paths import PROJECT_ROOT, data_dir
from backend.db.util import new_uuid, utc_now_iso

BACKEND_DIR = PROJECT_ROOT / "backend"
ERROR_SUMMARY_MAX = 500
SUBPROCESS_TAIL_MAX = 2000


class JobSkipped(Exception):
    """任务主动跳过(依赖模块不存在等),不算失败。"""


# ── 包内函数任务(evaluate / import_gold / manifest) ──────────────────


def _job_prediction_register() -> dict:
    """把 gold_wdl_predictions 导入预测登记簿(幂等,已导入的跳过)。"""
    from backend.cli.import_gold_predictions import import_gold
    from backend.db.connections import connect_ro

    conn_core = connect_ro("core")
    conn_platform = connect_rw("platform")
    try:
        with tx(conn_platform):
            stats = import_gold(conn_platform, conn_core)
    finally:
        conn_core.close()
        conn_platform.close()
    return {
        "input_count": stats["total"],
        "output_count": stats["draft"] + stats["legacy_unverified"],
        "meta": stats,
    }


def _job_postmatch_settle() -> dict:
    """结算已完赛快照 + 计算正式口径评估指标(无正式样本时诚实报告 0)。"""
    from backend.cli.evaluate_predictions import evaluate
    from backend.db.connections import connect_ro

    conn_core = connect_ro("core")
    conn_platform = connect_rw("platform")
    try:
        result = evaluate(conn_platform, conn_core, model_version_id=None, notes="worker postmatch_settle")
    finally:
        conn_core.close()
        conn_platform.close()
    meta = {k: v for k, v in result.items() if k != "calibration"}
    if result.get("sample_size", 0) == 0:
        meta["note"] = "暂无符合正式口径的样本(official+locked+pre-kickoff+已结算),未写入评估"
    return {
        "input_count": result.get("sample_size", 0),
        "output_count": result.get("settled_now", 0),
        "meta": meta,
    }


def _job_metrics_rebuild() -> dict:
    """生成/追加当日正式预测 manifest(内容不变则不新增版本)。"""
    from backend.commands.predictions import build_daily_manifest

    conn = connect_rw("platform")
    try:
        with tx(conn):
            result = build_daily_manifest(conn, utc_now_iso()[:10])
    finally:
        conn.close()
    return {"output_count": result.get("entries", 0), "meta": result}


# ── 任务注册表 ──────────────────────────────────────────────────────
# 每项:kind ∈ {"fn", "subprocess", "optional"}
#   fn         → {"fn": callable}
#   subprocess → {"argv": [...], "cwd": str, "require_env": (...)}
#   optional   → {"candidates": [(相对 PROJECT_ROOT 的探测文件, python -m 模块名), ...]}
#                第一个存在的候选以子进程方式运行;都不存在 → skipped + reason。

REGISTRY: dict[str, dict] = {
    "schedule_sync": {
        "kind": "subprocess",
        "argv": [sys.executable, "ingest/ingest_future_fixtures.py", "--league-id", "47", "--season", "2026/2027"],
        "cwd": str(BACKEND_DIR),
        "require_env": ("THORDATA_PROXY",),
        "max_attempts": 3,
        "timeout_seconds": 900,
        "backoff_seconds": 60,
        "description": "同步未来赛程(FotMob fixtures → dim_match,需住宅代理)",
    },
    "fotmob_incremental": {
        "kind": "subprocess",
        # scheduler.py 的完整步 1(--skip-scrape 跳过的那步):NotStarted→Finish 增量全量落库。
        # 步 2/3/4 由链上 silver_build / model_predict 独立任务承担,不在此重复。
        "argv": [sys.executable, "-c", "import scheduler; scheduler.step1_ingest_newly_finished(47, '2026/2027')"],
        "cwd": str(BACKEND_DIR),
        "require_env": ("THORDATA_PROXY",),
        "max_attempts": 2,
        "timeout_seconds": 3600,
        "backoff_seconds": 120,
        "description": "增量抓取新完赛场次(比分+xG+事件+阵容,需住宅代理)",
    },
    "nowgoal_snapshot": {
        "kind": "optional",
        "candidates": [("backend/cli/poll_nowgoal.py", "backend.cli.poll_nowgoal")],
        "max_attempts": 2,
        "timeout_seconds": 900,
        "backoff_seconds": 60,
        "description": "NowGoal 单轮采集:日程→实体映射→hash-diff 赔率快照(poll_nowgoal)",
    },
    "entity_resolution": {
        "kind": "optional",
        "candidates": [
            ("backend/cli/resolve_entities.py", "backend.cli.resolve_entities"),
            ("backend/odds/entity_resolution.py", "backend.odds.entity_resolution"),
        ],
        "max_attempts": 2,
        "timeout_seconds": 600,
        "backoff_seconds": 30,
        "description": "实体对齐链位:当前已内联在 nowgoal_snapshot(poll_nowgoal)内执行;独立 CLI 出现前记 skipped",
    },
    "silver_build": {
        "kind": "subprocess",
        "argv": [sys.executable, "silver/build_silver.py"],
        "cwd": str(BACKEND_DIR),
        "max_attempts": 1,
        "timeout_seconds": 1800,
        "backoff_seconds": 0,
        "description": "Bronze → Silver 聚合(按联赛+赛季 DELETE+INSERT,幂等)",
    },
    "model_predict": {
        "kind": "subprocess",
        "argv": [sys.executable, "models/predict_wdl_future.py"],
        "cwd": str(BACKEND_DIR),
        "max_attempts": 1,
        "timeout_seconds": 1800,
        "backoff_seconds": 0,
        "description": "对 NotStarted 场次重算 WDL 概率(固定模型参数,只更新 rolling 输入)",
    },
    "prediction_register": {
        "kind": "fn",
        "fn": _job_prediction_register,
        "max_attempts": 1,
        "timeout_seconds": 600,
        "backoff_seconds": 0,
        "description": "gold_wdl_predictions → 预测登记簿(幂等导入)",
    },
    "analysis_bundle_build": {
        "kind": "optional",
        "candidates": [
            ("backend/studio/build_bundle.py", "backend.studio.build_bundle"),
            ("backend/studio/__main__.py", "backend.studio"),
        ],
        "max_attempts": 1,
        "timeout_seconds": 900,
        "backoff_seconds": 0,
        "description": "Studio 分析包构建(backend.studio 未交付时 skipped)",
    },
    "postmatch_settle": {
        "kind": "fn",
        "fn": _job_postmatch_settle,
        "max_attempts": 1,
        "timeout_seconds": 600,
        "backoff_seconds": 0,
        "description": "赛后结算 + 正式口径评估(无正式样本时如实报 0)",
    },
    "metrics_rebuild": {
        "kind": "fn",
        "fn": _job_metrics_rebuild,
        "max_attempts": 1,
        "timeout_seconds": 300,
        "backoff_seconds": 0,
        "description": "重建当日正式预测 manifest(内容不变不加版本)",
    },
}

DEFAULT_CHAIN = [
    "schedule_sync",
    "fotmob_incremental",
    "nowgoal_snapshot",
    "entity_resolution",
    "silver_build",
    "model_predict",
    "prediction_register",
    "analysis_bundle_build",
    "postmatch_settle",
    "metrics_rebuild",
]


def register_job(name: str, **spec) -> None:
    """注册/覆盖一个任务(测试注入临时任务用)。"""
    spec.setdefault("kind", "fn" if "fn" in spec else "subprocess")
    spec.setdefault("max_attempts", 1)
    spec.setdefault("timeout_seconds", 600)
    spec.setdefault("backoff_seconds", 0)
    REGISTRY[name] = spec


# ── 文件锁 ──────────────────────────────────────────────────────────


def _locks_dir() -> Path:
    d = data_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock_pid(path: Path):
    try:
        return int(json.loads(path.read_text()).get("pid"))
    except Exception:
        return None


def _acquire_lock(job_name: str):
    """成功返回锁文件 Path;锁被存活进程持有返回 None;陈锁(pid 已死)自动清理后重试。"""
    path = _locks_dir() / f"{job_name}.lock"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = _read_lock_pid(path)
            if pid is not None and _pid_alive(pid):
                return None  # 有效持有者(含本进程其他线程)
            try:
                path.unlink()  # 陈锁:持有进程已死(或锁文件损坏)
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"pid": os.getpid(), "job": job_name, "acquired_at": utc_now_iso()}))
        return path
    return None


def _release_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ── job_runs 读写 ───────────────────────────────────────────────────


def _truncate(text: str, limit: int = ERROR_SUMMARY_MAX) -> str:
    text = (text or "").strip()
    suffix = " …[truncated]"
    return text if len(text) <= limit else text[: limit - len(suffix)] + suffix


def _insert_run(conn, job_name, idempotency_key, max_attempts, status="pending",
                error_summary=None, meta=None, finished=False) -> str:
    run_id = new_uuid()
    now = utc_now_iso()
    with tx(conn):
        conn.execute(
            """INSERT INTO job_runs
               (id, job_name, idempotency_key, status, attempt, max_attempts,
                started_at, finished_at, error_summary, meta_json, created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
            (run_id, job_name, idempotency_key, status, max_attempts,
             now if finished else None, now if finished else None,
             error_summary, json.dumps(meta or {}, ensure_ascii=False), now),
        )
    return run_id


def _prepare_run(conn, job_name, idempotency_key, max_attempts) -> str:
    """新建 pending 行;幂等键已有(非成功或 force 重跑)则复位复用同一行(UNIQUE 约束)。"""
    if idempotency_key is not None:
        row = conn.execute(
            "SELECT id, meta_json FROM job_runs WHERE job_name=? AND idempotency_key=?",
            (job_name, idempotency_key),
        ).fetchone()
        if row:
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except ValueError:
                meta = {}
            meta["reruns"] = int(meta.get("reruns", 0)) + 1
            with tx(conn):
                conn.execute(
                    """UPDATE job_runs SET status='pending', attempt=1, max_attempts=?,
                       started_at=NULL, finished_at=NULL, input_count=NULL, output_count=NULL,
                       error_summary=NULL, meta_json=? WHERE id=?""",
                    (max_attempts, json.dumps(meta, ensure_ascii=False), row["id"]),
                )
            return row["id"]
    return _insert_run(conn, job_name, idempotency_key, max_attempts)


def _mark_running(conn, run_id, attempt) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE job_runs SET status='running', attempt=?, started_at=COALESCE(started_at, ?) WHERE id=?",
            (attempt, utc_now_iso(), run_id),
        )


def _finish_run(conn, run_id, status, error_summary=None, input_count=None,
                output_count=None, meta_update=None) -> None:
    row = conn.execute("SELECT meta_json FROM job_runs WHERE id=?", (run_id,)).fetchone()
    try:
        meta = json.loads(row["meta_json"] or "{}") if row else {}
    except ValueError:
        meta = {}
    meta.update(meta_update or {})
    with tx(conn):
        conn.execute(
            """UPDATE job_runs SET status=?, finished_at=?, error_summary=?,
               input_count=?, output_count=?, meta_json=? WHERE id=?""",
            (status, utc_now_iso(), error_summary, input_count, output_count,
             json.dumps(meta, ensure_ascii=False, default=str), run_id),
        )


# ── 执行 ────────────────────────────────────────────────────────────


def _resolve_optional(spec: dict) -> list:
    """返回可执行 argv;所有候选都不存在时抛 JobSkipped。"""
    for rel_path, module in spec.get("candidates", []):
        if (PROJECT_ROOT / rel_path).exists():
            return [sys.executable, "-m", module]
    probed = ", ".join(p for p, _ in spec.get("candidates", []))
    raise JobSkipped(f"依赖模块尚未交付({probed} 均不存在),本步跳过")


def _run_subprocess(argv, cwd, timeout) -> dict:
    try:
        proc = subprocess.run(
            argv, cwd=cwd or str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"subprocess 超时({timeout}s):{' '.join(argv)}") from e
    meta = {
        "argv": argv,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-SUBPROCESS_TAIL_MAX:],
        "stderr_tail": proc.stderr[-SUBPROCESS_TAIL_MAX:],
    }
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        err = RuntimeError(f"exit={proc.returncode}: {detail[-ERROR_SUMMARY_MAX:]}")
        err.job_meta = meta
        raise err
    return {"meta": meta}


def _run_fn(fn, timeout) -> dict:
    """函数任务:线程执行 + 总时长兜底(超时后放弃等待,线程无法强杀)。"""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            raise RuntimeError(f"函数任务超时({timeout}s),已放弃等待(线程可能仍在收尾)") from None
    finally:
        executor.shutdown(wait=False)
    if not isinstance(result, dict):
        return {"meta": {"result": repr(result)}}
    return result


def _execute_once(spec: dict, timeout: int) -> dict:
    kind = spec.get("kind", "fn" if "fn" in spec else "subprocess")
    if kind == "fn":
        return _run_fn(spec["fn"], timeout)
    if kind == "optional":
        argv = _resolve_optional(spec)
        return _run_subprocess(argv, spec.get("cwd"), timeout)
    return _run_subprocess(spec["argv"], spec.get("cwd"), timeout)


def run_job(job_name: str, idempotency_key: str | None = None, force: bool = False) -> dict:
    """执行单个任务,返回 {"run_id", "status", "attempt", "error_summary", ...}。"""
    if job_name not in REGISTRY:
        raise KeyError(f"未注册的任务: {job_name!r}(--list 查看全部)")
    spec = REGISTRY[job_name]
    max_attempts = int(spec.get("max_attempts", 1))
    timeout = spec.get("timeout_seconds", 600)
    backoff = float(spec.get("backoff_seconds", 0))

    conn = connect_rw("platform")
    try:
        # 幂等:同 (job_name, key) 已成功且未 force → 本次记 skipped,不执行
        if idempotency_key is not None and not force:
            prior = conn.execute(
                "SELECT id, status FROM job_runs WHERE job_name=? AND idempotency_key=?",
                (job_name, idempotency_key),
            ).fetchone()
            if prior and prior["status"] == "succeeded":
                run_id = _insert_run(
                    conn, job_name, None, max_attempts, status="skipped", finished=True,
                    meta={"reason": "idempotency_key 已成功执行过",
                          "idempotency_key": idempotency_key, "dedupe_of": prior["id"]},
                )
                return {"run_id": run_id, "status": "skipped", "attempt": 1,
                        "error_summary": None, "reason": "idempotency_key 已成功执行过"}

        # 并发锁
        lock = _acquire_lock(job_name)
        if lock is None:
            holder = _read_lock_pid(_locks_dir() / f"{job_name}.lock")
            return {"run_id": None, "status": "locked", "attempt": 0,
                    "error_summary": f"lock 被 pid={holder} 持有,本次未执行"}

        try:
            run_id = _prepare_run(conn, job_name, idempotency_key, max_attempts)

            # 环境变量前置检查(缺失 → failed + 清晰 error,不崩溃、不重试)
            missing = [k for k in spec.get("require_env", ()) if not os.environ.get(k)]
            if missing:
                summary = _truncate(f"缺少环境变量 {', '.join(missing)},任务未执行(FotMob 抓取需要住宅代理)")
                _finish_run(conn, run_id, "failed", error_summary=summary)
                return {"run_id": run_id, "status": "failed", "attempt": 1, "error_summary": summary}

            attempt = 0
            last_error = None
            while attempt < max_attempts:
                attempt += 1
                _mark_running(conn, run_id, attempt)
                try:
                    result = _execute_once(spec, timeout)
                except JobSkipped as e:
                    _finish_run(conn, run_id, "skipped", meta_update={"reason": str(e)})
                    return {"run_id": run_id, "status": "skipped", "attempt": attempt,
                            "error_summary": None, "reason": str(e)}
                except Exception as e:  # noqa: BLE001 — worker 边界,必须兜住一切任务异常
                    last_error = _truncate(f"{type(e).__name__}: {e}")
                    meta = getattr(e, "job_meta", None)
                    if attempt >= max_attempts:
                        _finish_run(conn, run_id, "failed", error_summary=last_error,
                                    meta_update=meta)
                        return {"run_id": run_id, "status": "failed", "attempt": attempt,
                                "error_summary": last_error}
                    if backoff > 0:
                        time.sleep(backoff * attempt)
                    continue
                _finish_run(
                    conn, run_id, "succeeded",
                    input_count=result.get("input_count"),
                    output_count=result.get("output_count"),
                    meta_update=result.get("meta"),
                )
                return {"run_id": run_id, "status": "succeeded", "attempt": attempt,
                        "error_summary": None,
                        "input_count": result.get("input_count"),
                        "output_count": result.get("output_count"),
                        "meta": result.get("meta")}
            # 理论不可达
            return {"run_id": run_id, "status": "failed", "attempt": attempt,
                    "error_summary": last_error}
        finally:
            _release_lock(lock)
    finally:
        conn.close()


def run_chain(names: list[str] | None = None, start_from: str | None = None) -> list[dict]:
    """按顺序执行任务链;某步 failed/locked 后,后续步骤记 skipped(依赖检查)。"""
    chain = list(names) if names else list(DEFAULT_CHAIN)
    if start_from is not None:
        if start_from not in chain:
            raise KeyError(f"--from 步骤 {start_from!r} 不在链 {chain} 中")
        chain = chain[chain.index(start_from):]

    results = []
    failed_step = None
    conn = None
    for name in chain:
        if failed_step is not None:
            if conn is None:
                conn = connect_rw("platform")
            reason = f"上游 {failed_step} failed,依赖不满足"
            run_id = _insert_run(
                conn, name, None, int(REGISTRY.get(name, {}).get("max_attempts", 1)),
                status="skipped", error_summary=_truncate(reason), finished=True,
                meta={"reason": reason, "chain_upstream_failed": failed_step},
            )
            results.append({"job": name, "run_id": run_id, "status": "skipped",
                            "error_summary": reason})
            continue
        res = run_job(name)
        results.append({"job": name, **res})
        if res["status"] in ("failed", "locked"):
            failed_step = name
    if conn is not None:
        conn.close()
    return results


# ── CLI ─────────────────────────────────────────────────────────────


def _print_result(prefix: str, res: dict) -> None:
    line = {k: v for k, v in res.items() if k != "meta" and v is not None}
    print(f"{prefix}{json.dumps(line, ensure_ascii=False)}")
    meta = res.get("meta")
    if meta:
        compact = {k: v for k, v in meta.items() if k not in ("stdout_tail", "stderr_tail", "argv")}
        if compact:
            print(f"  meta: {json.dumps(compact, ensure_ascii=False, default=str)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="allwin 轻量 Worker(job_runs 全生命周期落 platform.db)")
    ap.add_argument("--job", help="执行单个任务")
    ap.add_argument("--key", default=None, help="幂等键(同键已成功 → skipped)")
    ap.add_argument("--force", action="store_true", help="忽略幂等键强制重跑")
    ap.add_argument("--chain", action="store_true", help="按默认顺序执行任务链")
    ap.add_argument("--from", dest="from_step", default=None, help="链从指定步骤开始(配合 --chain)")
    ap.add_argument("--list", action="store_true", dest="list_jobs", help="列出注册任务")
    args = ap.parse_args(argv)

    if args.list_jobs:
        print(f"{'job':<24} {'kind':<12} {'attempts':<9} {'timeout':<8} description")
        for name in DEFAULT_CHAIN:
            spec = REGISTRY[name]
            print(f"{name:<24} {spec.get('kind', 'fn'):<12} {spec.get('max_attempts', 1):<9} "
                  f"{spec.get('timeout_seconds', 600):<8} {spec.get('description', '')}")
        extra = [n for n in REGISTRY if n not in DEFAULT_CHAIN]
        for name in extra:
            spec = REGISTRY[name]
            print(f"{name:<24} {spec.get('kind', 'fn'):<12} {spec.get('max_attempts', 1):<9} "
                  f"{spec.get('timeout_seconds', 600):<8} {spec.get('description', '')} (不在默认链)")
        return 0

    if args.job:
        res = run_job(args.job, idempotency_key=args.key, force=args.force)
        _print_result(f"[{args.job}] ", res)
        return 0 if res["status"] in ("succeeded", "skipped") else 1

    if args.chain:
        results = run_chain(start_from=args.from_step)
        rc = 0
        for res in results:
            _print_result(f"[{res['job']}] ", res)
            if res["status"] in ("failed", "locked"):
                rc = 1
        return rc

    ap.error("需要 --job <name>、--chain 或 --list")
    return 2


if __name__ == "__main__":
    sys.exit(main())
