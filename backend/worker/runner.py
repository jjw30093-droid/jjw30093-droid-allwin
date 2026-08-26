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
  --from <step> 支持从中间步骤重跑。PIPELINE_REDESIGN_V2 P3 起,生产没有任何
  定时器再周期性调用 --chain——7 个独立 systemd 定时器各自单任务
  `--job <name>` 或用 group_runner 顺序执行互不依赖的任务组(见
  backend/worker/group_runner.py),--chain 现在只是人工全量重跑用的手动
  逃生舱。
- 核心链任务(采集/实体解析/两类 Silver/analysis bundle)全部为真实注册任务
  (CLAUDE.md §13):外部凭证缺失时诚实记 failed + 原因,不以"模块不存在"跳过;
  "optional" kind 仅保留给测试注入使用。

- 失败告警(数据管道重建 Phase 5):run_job 的失败分支在 _finish_run 落库之后
  经 backend.notify 推 CRITICAL(pipeline_step_failure / proxy_unavailable);
  链上 cascade-skip 的步骤不执行、不告警——只有最初失败那一步会推。
- 质量门 pipeline_gates 挂链尾;daily_digest 不进链(allwin-digest.timer 每天
  23:30 Asia/Shanghai 以 --key <北京日期> 幂等调度)。

用法:
  python -m backend.worker.runner --list
  python -m backend.worker.runner --job silver_build
  python -m backend.worker.runner --job daily_digest --key 2026-08-10
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


def _job_reco_auto_settle() -> dict:
    """自动结算「每日精选」推荐单(独立于模型预测评估之外,CLAUDE.md §9.1)。

    只处理 published 单里已正式完赛(status='Finish' 且比分非空)、全部由真实
    溯源腿(entry_type='provenance_bound')组成的单,整单原子结算;人工已结算
    (settle_source='manual')、混有 legacy_manual 腿、或暂不可判定(比赛未
    完赛/角球等统计缺失)的单本轮跳过,不猜测——原因写进 meta.skip_reasons,
    可追溯到具体 slip_id/leg_id/match_id(见 backend/commands/reco_auto_settle.py)。

    与 _job_postmatch_settle(模型预测赛后评估)彻底独立:不同 job_name、不同
    注册函数、不共享连接生命周期、不复用彼此的判定逻辑——本函数只做"串起来"
    这一件事,不重新实现结算算术。
    """
    from backend.commands.reco_auto_settle import run_auto_settle
    from backend.db.connections import connect_ro

    conn_core = connect_ro("core")
    conn_platform = connect_rw("platform")
    try:
        result = run_auto_settle(conn_platform, conn_core, actor="system")
    finally:
        conn_core.close()
        conn_platform.close()
    return {"output_count": result["settled_slips"], "meta": result}


def _job_analysis_bundle_build() -> dict:
    """为未开赛比赛构建 analysis_bundle(详情页与 Studio 共用的同一 builder)。

    验证并预热链路:窗口内(按 kickoff/日期最近的)NotStarted 场次逐场构建,
    单场失败继续其余;bundle 构建为只读操作,不写库。
    """
    from backend.db.connections import connect_ro
    from backend.studio.bundle import build_analysis_bundle

    conn_core = connect_ro("core")
    conn_platform = connect_ro("platform")
    try:
        conn_odds = connect_ro("odds")
    except Exception:  # noqa: BLE001 — odds.db 尚未建库时 bundle 允许 None
        conn_odds = None
    built = 0
    errors: list[str] = []
    try:
        rows = conn_core.execute(
            """SELECT Match_ID FROM dim_match WHERE status='NotStarted'
               ORDER BY COALESCE(kickoff_at_utc, Date), Match_ID LIMIT 50"""
        ).fetchall()
        for r in rows:
            try:
                bundle = build_analysis_bundle(
                    conn_core, conn_platform, conn_odds, int(r["Match_ID"])
                )
                if bundle is not None:
                    built += 1
            except Exception as exc:  # noqa: BLE001 — 单场失败不拖垮整轮
                errors.append(f"match {r['Match_ID']}: {type(exc).__name__}: {exc}")
    finally:
        conn_core.close()
        conn_platform.close()
        if conn_odds is not None:
            conn_odds.close()
    meta = {"errors": errors[:10]} if errors else {}
    return {"input_count": len(rows), "output_count": built, "meta": meta}


def _job_pipeline_gates() -> dict:
    """质量门(链尾):只读体检三库,违反的门经 backend.notify 发告警。

    门的"发现问题"不算本任务失败(避免再触发一条 pipeline_step_failure 的
    重复告警);任务失败语义只留给检查本身崩溃。
    """
    from backend.cli.pipeline_gates import run as run_gates

    report = run_gates()
    violations = [g for g in report["gates"] if g["level"] != "OK"]
    return {
        "input_count": len(report["gates"]),
        "output_count": len(violations),
        "meta": {"level": report["level"],
                 "violated": [{k: g.get(k) for k in ("gate", "level")} for g in violations]},
    }


def _job_daily_digest() -> dict:
    """每日汇总(INFO 一条):近 24h 赛程同步/赔率快照/轮询尝试/失败任务/已推告警;
    顺带清理 30 天前的 poll_attempt_log(append-only 表的保留策略)。"""
    from datetime import datetime, timedelta, timezone

    from backend import notify as notify_mod
    from backend.db.connections import connect_ro

    now = utc_now_iso()
    day = now[:10]
    now_dt = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(timezone.utc)
    lo24 = (now_dt - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    keep_floor = (now_dt - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    digest: dict = {"date": day}
    conn_odds = connect_rw("odds")
    try:
        digest["fixture_sync_by_verdict"] = {
            r["verdict"]: r["n"] for r in conn_odds.execute(
                "SELECT verdict, COUNT(*) AS n FROM fixture_sync_ledger "
                "WHERE julianday(run_at) >= julianday(?) GROUP BY verdict", (lo24,))
        }
        digest["fixtures_written_rows"] = conn_odds.execute(
            "SELECT COALESCE(SUM(written_rows),0) FROM fixture_sync_ledger "
            "WHERE julianday(run_at) >= julianday(?)", (lo24,)).fetchone()[0]
        digest["odds_snapshots"] = conn_odds.execute(
            "SELECT COUNT(*) FROM bronze_ng_odds_snap "
            "WHERE julianday(observed_at) >= julianday(?)", (lo24,)).fetchone()[0]
        digest["poll_attempts"] = conn_odds.execute(
            "SELECT COUNT(*) FROM poll_attempt_log "
            "WHERE julianday(attempted_at) >= julianday(?)", (lo24,)).fetchone()[0]
        with tx(conn_odds):
            cur = conn_odds.execute(
                "DELETE FROM poll_attempt_log WHERE julianday(attempted_at) < julianday(?)",
                (keep_floor,))
            digest["attempt_log_purged"] = cur.rowcount
    finally:
        conn_odds.close()

    conn_p = connect_ro("platform")
    try:
        digest["job_failures_24h"] = {
            r["job_name"]: r["n"] for r in conn_p.execute(
                "SELECT job_name, COUNT(*) AS n FROM job_runs "
                "WHERE status='failed' AND julianday(created_at) >= julianday(?) "
                "GROUP BY job_name", (lo24,))
        }
        digest["alerts_sent_today"] = conn_p.execute(
            "SELECT COUNT(*) FROM pipeline_alerts WHERE notify_result='sent' "
            "AND created_at >= ?", (day + "T00:00:00Z",)).fetchone()[0]
    finally:
        conn_p.close()

    body_lines = [
        f"赛程同步(24h): {digest['fixture_sync_by_verdict']} / 写入 {digest['fixtures_written_rows']} 行",
        f"赔率快照(24h): {digest['odds_snapshots']} 条;轮询尝试 {digest['poll_attempts']} 次",
        f"失败任务(24h): {digest['job_failures_24h'] or '无'}",
        f"今日已推送告警: {digest['alerts_sent_today']} 条",
    ]
    alert = notify_mod.notify(
        level="INFO", source="daily_digest",
        title=f"allwin 管道日报 {day}",
        body="\n".join(body_lines),
        dedup_key=f"daily_digest:{day}",
    )
    return {"output_count": 1, "meta": {**digest, "alert": alert}}


def _watermark_core_silver_build() -> dict:
    """core_silver_build 只聚合 dim_match.status='Finish' 的场次(见
    backend/silver/build_silver.py),按联赛+赛季全量 DELETE+INSERT——没有
    新完赛比赛时重跑毫无意义。dim_match/fact_team_match_stats/fact_match_events
    三张 Bronze 表本身没有任何时间戳列(已核实实际 schema),无法做真正的
    "自上次以来新增行"增量水位;退而求其次用"当前 Finish 总数"作粗粒度信号
    ——已知局限:对已是 Finish 的比赛做比分/统计修正而不改变总数时,本水位
    检测不到(不隐瞒这个限制,见本 phase 改动说明)。"""
    from backend.db.connections import connect_ro

    conn = connect_ro("core")
    try:
        n = conn.execute("SELECT COUNT(*) FROM dim_match WHERE status='Finish'").fetchone()[0]
    finally:
        conn.close()
    return {"finished_matches": int(n)}


def _watermark_odds_silver_build() -> dict:
    """odds_silver_build 每次全量重扫三张 odds Bronze 表,并且
    build_odds_moves()(backend/silver/odds_moves.py)额外要求
    dim_match_xref.review_status IN ('auto_ok','confirmed')
    (_ACTIVE_XREF_STATUSES)才为该场比赛产出行——这是第二个独立输入,
    2026-08-17 真实发现:已落库的 Bronze 快照,若当时 xref 还是
    needs_review,之后管理员经 /api/v1/admin/xref/{id}/confirm 把它转成
    auto_ok/confirmed 时不产生任何新 Bronze 行,只看三张 Bronze 表
    MAX(id) 的水位对此完全不可见,会让本来已经能产出的历史快照悄悄卡在
    Silver 之外。加入活跃 xref 集合的 MAX(updated_at)(confirm/reject 接口
    真实更新这一列)作为第二个水位分量,覆盖这条独立路径。"""
    from backend.db.connections import connect_ro

    conn = connect_ro("odds")
    try:
        odds_max = conn.execute("SELECT COALESCE(MAX(id),0) FROM bronze_ng_odds_snap").fetchone()[0]
        lineup_max = conn.execute("SELECT COALESCE(MAX(id),0) FROM bronze_fm_lineup_snap").fetchone()[0]
        sideline_max = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM bronze_fm_sideline_snap").fetchone()[0]
        xref_updated_at = conn.execute(
            "SELECT COALESCE(MAX(updated_at),'') FROM dim_match_xref"
            " WHERE provider='nowgoal' AND review_status IN ('auto_ok','confirmed')"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "bronze_ng_odds_snap_max_id": int(odds_max),
        "bronze_fm_lineup_snap_max_id": int(lineup_max),
        "bronze_fm_sideline_snap_max_id": int(sideline_max),
        "active_xref_max_updated_at": str(xref_updated_at),
    }


def _alert_job_failure(conn, run_id, job_name, error_summary,
                       source: str = "pipeline_step_failure") -> None:
    """任务失败 → CRITICAL 告警(调用点保证已先 _finish_run 落 job_runs)。

    notify 永不抛;其结果(persisted/result)写回 job_runs.meta_json——
    这是"告警器自己坏了"三条暴露路径的第二条(第一条 stderr 标记在 notify 内,
    第三条 ops_check 的 pending 检查)。链上 cascade-skip 的步骤不经过 run_job
    执行分支,天然只有最初失败的那一步会到这里。
    """
    from backend import notify as notify_mod

    res = notify_mod.notify(
        level="CRITICAL", source=source,
        title=f"任务失败: {job_name}",
        body=f"job={job_name}\nrun_id={run_id}\n{error_summary or ''}",
        dedup_key=f"{source}:{job_name}",
        conn=conn,
    )
    try:
        row = conn.execute("SELECT meta_json FROM job_runs WHERE id=?", (run_id,)).fetchone()
        try:
            meta = json.loads(row["meta_json"] or "{}") if row else {}
        except ValueError:
            meta = {}
        meta["alert"] = {"persisted": res.get("persisted"), "result": res.get("result")}
        with tx(conn):
            conn.execute("UPDATE job_runs SET meta_json=? WHERE id=?",
                         (json.dumps(meta, ensure_ascii=False, default=str), run_id))
    except Exception:  # noqa: BLE001 — 告警回写失败不能反过来毁掉任务记录
        print(f"ALLWIN_ALERT_PERSIST_FAILED stage=job_meta run_id={run_id}", file=sys.stderr)


# ── 任务注册表 ──────────────────────────────────────────────────────
# 每项:kind ∈ {"fn", "subprocess", "optional"}
#   fn         → {"fn": callable}
#   subprocess → {"argv": [...], "cwd": str, "require_env": (...)}
#   optional   → {"candidates": [(相对 PROJECT_ROOT 的探测文件, python -m 模块名), ...]}
#                第一个存在的候选以子进程方式运行;都不存在 → skipped + reason。

REGISTRY: dict[str, dict] = {
    "schedule_sync_multi": {
        "kind": "subprocess",
        # 数据管道重建 Phase 6:取代旧 schedule_sync 的单联赛硬编码(47/2026/2027)。
        # 全联赛 T+7 赛程刷新,poll_state 每联赛 6h 节流(15 分钟 chain 反复触发
        # 也不会多打 FotMob),反退化/降级门禁在 CLI 内部(fixture_sync_ledger)。
        "argv": [sys.executable, "-m", "backend.cli.sync_fixtures_window", "--due"],
        "cwd": str(PROJECT_ROOT),
        "require_env": ("THORDATA_PROXY",),
        "max_attempts": 2,
        "timeout_seconds": 900,
        "backoff_seconds": 60,
        "description": "T+7 赛程同步:全联赛赛季发现 + 反退化门禁 + 6h/联赛节流(需住宅代理)",
    },
    "fotmob_incremental_multi": {
        "kind": "subprocess",
        # scheduler.py 的完整步 1(NotStarted→Finish 增量全量落库),按 LEAGUE_META
        # 全联赛遍历、每联赛 6h 节流;步 2/3/4 由链上 silver_build / model_predict
        # 独立任务承担,不在此重复。
        "argv": [sys.executable, "-m", "backend.cli.fotmob_incremental_multi", "--due"],
        "cwd": str(PROJECT_ROOT),
        "require_env": ("THORDATA_PROXY",),
        # PIPELINE_REDESIGN_V2 P4 起 max_attempts=1(此前是 2/backoff 120s)——
        # 比赛级重试计数(backend.ingest.postmatch_retry)才是正确的责任层,
        # 任务级快速重试会在同一个 allwin-postmatch 30 分钟 tick 内让仍
        # stale 的比赛被重复计数,提前吃掉 MAX_ATTEMPTS 安全余量。
        "max_attempts": 1,
        "timeout_seconds": 3600,
        "description": "增量抓取新完赛场次(全联赛,比分+xG+事件+阵容,需住宅代理)",
    },
    "nowgoal_snapshot": {
        "kind": "subprocess",
        "argv": [sys.executable, "-m", "backend.cli.poll_nowgoal", "--due"],
        "cwd": str(PROJECT_ROOT),
        "max_attempts": 2,
        "timeout_seconds": 900,
        "backoff_seconds": 60,
        "description": "NowGoal 窗口到期采集:72h 内精确 kickoff 比赛,15/5 分钟节流,hash-diff 落库",
    },
    "fotmob_snapshot": {
        "kind": "subprocess",
        # 2026-08-24 真实事故修复:--write-match-details 开关早已实现
        # (backend/cli/poll_fotmob_snapshots.py::_write_match_details,COALESCE
        # 窄 UPDATE),但这里的 argv 一直漏了它——该任务跑了 1.4 万+ 次,每轮
        # 抓回整份 pageProps 后只留 lineup/sidelined 两个子树,场馆/天气/球队
        # 配色当场丢弃,导致 dim_match 里 Venue_*/Weather_Description 非空数
        # 为 0、配色仅 1 行。"实现了 CLI 开关但没在 runner argv 里接线"与
        # "根本没实现"在生产上等价;tests/backend/test_worker_argv.py 断言
        # 此开关永远在场。
        "argv": [
            sys.executable,
            "-m",
            "backend.cli.poll_fotmob_snapshots",
            "--due",
            "--write-match-details",
        ],
        "cwd": str(PROJECT_ROOT),
        "require_env": ("THORDATA_PROXY",),
        "max_attempts": 2,
        # 2026-08-18 候选池从 24h 放宽到 168h(§6.3)后,首轮 due 场次约 97 场
        # 串行请求:fotmob_client 是 timeout=25 + hard_timeout=35s + max_retries=3
        # 指数退避,单场最坏约 113s。97 × 113s ≈ 10970s 的理论上限太夸张,但
        # 只要 5 场硬失败就能吃满旧的 900s;留够首轮回填的余量,不靠"被超时
        # 杀掉是安全的"这种默认预期。
        "timeout_seconds": 1800,
        "backoff_seconds": 60,
        "description": "FotMob 阵容/伤停快照:同场同轮单次 payload,三快照共用 observed_at(需住宅代理)",
    },
    "entity_resolution": {
        "kind": "subprocess",
        "argv": [sys.executable, "-m", "backend.cli.resolve_entities"],
        "cwd": str(PROJECT_ROOT),
        "max_attempts": 2,
        "timeout_seconds": 600,
        "backoff_seconds": 30,
        "description": "实体解析:全联赛别名种子 + xref 审核状态汇报(新行解析内联在采集轮)",
    },
    "core_silver_build": {
        "kind": "subprocess",
        "argv": [sys.executable, "silver/build_silver.py"],
        "cwd": str(BACKEND_DIR),
        "max_attempts": 1,
        "timeout_seconds": 1800,
        "backoff_seconds": 0,
        "watermark_fn": _watermark_core_silver_build,
        "description": "core Bronze → Silver 聚合(按联赛+赛季 DELETE+INSERT,幂等;水位守卫:无新完赛则跳过)",
    },
    "odds_silver_build": {
        "kind": "subprocess",
        "argv": [sys.executable, "-m", "backend.cli.build_odds_silver"],
        "cwd": str(PROJECT_ROOT),
        "max_attempts": 1,
        "timeout_seconds": 900,
        "backoff_seconds": 0,
        "watermark_fn": _watermark_odds_silver_build,
        "description": "odds Bronze → 变化点/时间共现(UNIQUE 幂等,needs_review 映射不产出;水位守卫:无新 Bronze 行则跳过)",
    },
    "analysis_bundle_build": {
        "kind": "fn",
        "fn": _job_analysis_bundle_build,
        "max_attempts": 1,
        "timeout_seconds": 900,
        "backoff_seconds": 0,
        "description": "analysis_bundle 构建:窗口内 NotStarted 场次逐场构建(详情页/Studio 共用 builder)",
    },
    "reco_auto_settle": {
        "kind": "fn",
        "fn": _job_reco_auto_settle,
        "max_attempts": 1,
        "timeout_seconds": 300,
        "backoff_seconds": 0,
        "description": "自动结算「每日精选」推荐单(已完赛+真实溯源腿;人工已结算/混合单/不可判定单本轮跳过,与模型评估彻底独立)",
    },
    "pipeline_gates": {
        "kind": "fn",
        "fn": _job_pipeline_gates,
        "max_attempts": 1,
        "timeout_seconds": 300,
        "backoff_seconds": 0,
        "description": "质量门检查(赛程窗口/退化/实体解析/赔率与收盘覆盖/公司口径/WAF,违反发告警)",
    },
    "daily_digest": {
        "kind": "fn",
        "fn": _job_daily_digest,
        "max_attempts": 1,
        "timeout_seconds": 300,
        "backoff_seconds": 0,
        "description": "每日汇总推送(入库场次/赛程/快照/失败步骤;--key <日期> 幂等,由 allwin-digest.timer 调度)",
    },
    "physical_stats_poll": {
        "kind": "subprocess",
        # 体能统计(physical_metrics_*)迟到补采,daily_digest 同类的独立
        # timer 任务——不进 DEFAULT_CHAIN、不挂在 7 个既有定时器任何一个上
        # (CLAUDE.md §13):候选范围是英超 47 + 欧冠 42(42 当前 dim_match
        # 零行,是占位,今天空转),kickoff+6h/12h/24h 三个固定检查点,与 7
        # 个定时器各自的既有语义(赛程/赔率/阵容/赛后链/派生/维护)都不同构,
        # 单独一个 allwin-physical-stats.timer。
        "argv": [sys.executable, "-m", "backend.cli.poll_physical_stats", "--due"],
        "cwd": str(PROJECT_ROOT),
        "max_attempts": 1,
        "timeout_seconds": 900,
        "backoff_seconds": 0,
        "description": "体能统计(physical_metrics_distance_covered)迟到补采:kickoff+6h/12h/24h 三次检查点,双队达标即停,耗尽告警一次(英超 47 + 欧冠 42 占位)",
    },
    "standings_refresh_poll": {
        "kind": "subprocess",
        # 联赛积分榜(fact_league_table)迟到刷新,physical_stats_poll 同类的
        # 独立 timer 任务——不进 DEFAULT_CHAIN、不挂在 7 个既有定时器任何一个
        # 上(CLAUDE.md §13):候选范围是 LEAGUE_META 全部联赛(单一真源,
        # 2026-08-26 起不再是只有英超),触发条件是"该联赛+赛季最近一场完赛
        # 比赛开球后 6 小时",不是有限次检查点,也不同构于任何既有定时器的
        # 语义(赛程/赔率/阵容/赛后链/派生/维护)。每轮最多真正刷新 5 个联赛,
        # 其余顺延到下一轮(见 standings_refresh_poll.py::MAX_REFRESHES_PER_RUN)。
        "argv": [sys.executable, "-m", "backend.cli.poll_standings", "--due"],
        "cwd": str(PROJECT_ROOT),
        "max_attempts": 1,
        "timeout_seconds": 900,
        "backoff_seconds": 0,
        "description": "联赛积分榜(fact_league_table)迟到刷新:最近一场完赛比赛开球+6h 到期即重新拉取一次(全部联赛,每轮最多刷新 5 个)",
    },
}

DEFAULT_CHAIN = [
    "schedule_sync_multi",
    "fotmob_incremental_multi",
    "nowgoal_snapshot",
    "fotmob_snapshot",
    "entity_resolution",
    "core_silver_build",
    "odds_silver_build",
    "analysis_bundle_build",
    "reco_auto_settle",
    # 必须排最后:质量门"发现问题"通过告警表达,不通过任务失败表达——
    # 即便它 failed(检查本身崩溃),也没有下游可被 cascade-skip。
    "pipeline_gates",
]

# 注册了但不进默认链的任务(tests/backend/test_job_order.py 的白名单——
# 杜绝"注册了却永远不被调度"的孤儿任务):
# - silver_build:core_silver_build 的兼容别名(见下方);
# - daily_digest:由 allwin-digest.timer 每天 23:30(Asia/Shanghai)独立调度,
#   --key <北京日期> 幂等,天然每天恰好一次,不挂在任何 DEFAULT_CHAIN 的
#   周期性定时器上(和 7 个新定时器一样,是自己独立的 timer)。
# - physical_stats_poll:由 allwin-physical-stats.timer 每 30 分钟独立调度
#   (与 daily_digest 同一先例——注册在 Worker 里但不挂链、不占用 7 个既有
#   定时器中的任何一个,自己单独一个 timer)。
# - standings_refresh_poll:由 allwin-standings.timer 每 30 分钟独立调度,
#   同一先例——判断"最近一场完赛比赛开球+6h 是否已过、且尚未刷新"不需要
#   比 30 分钟更密的检查频率,与 physical_stats_poll 选取同一节奏。
NON_CHAIN_JOBS = frozenset({
    "silver_build", "daily_digest", "physical_stats_poll", "standings_refresh_poll",
})

# 兼容别名:旧名 silver_build 指向 core_silver_build(不在默认链中)
REGISTRY["silver_build"] = REGISTRY["core_silver_build"]


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


def _last_succeeded_meta(conn, job_name: str) -> dict | None:
    row = conn.execute(
        "SELECT meta_json FROM job_runs WHERE job_name=? AND status='succeeded' "
        "ORDER BY finished_at DESC LIMIT 1",
        (job_name,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["meta_json"] or "{}")
    except ValueError:
        return {}


def _execute_once(spec: dict, timeout: int, conn=None, job_name: str | None = None) -> dict:
    """水位守卫(PIPELINE_REDESIGN_V2 P4,3.4):spec 声明 watermark_fn 时,
    先算当前信号,与"上一次成功执行"落在 job_runs.meta_json 里的信号比较——
    未变化则复用既有的 JobSkipped → status='skipped' 路径(与 optional kind
    候选模块缺失时的跳过是同一条已测试过的路径),不新建一套并行状态机;
    变化(或从未成功执行过)时照常真跑,并把本次信号写回结果 meta 供下次比较。
    """
    kind = spec.get("kind", "fn" if "fn" in spec else "subprocess")
    watermark_fn = spec.get("watermark_fn")
    current_watermark = None
    if watermark_fn is not None:
        current_watermark = watermark_fn()
        if conn is not None and job_name is not None:
            prior_meta = _last_succeeded_meta(conn, job_name)
            if prior_meta is not None and prior_meta.get("watermark") == current_watermark:
                raise JobSkipped(f"watermark 未变化(自上次成功运行以来无新数据):{current_watermark}")

    if kind == "fn":
        result = _run_fn(spec["fn"], timeout)
    elif kind == "optional":
        argv = _resolve_optional(spec)
        result = _run_subprocess(argv, spec.get("cwd"), timeout)
    else:
        result = _run_subprocess(spec["argv"], spec.get("cwd"), timeout)

    if watermark_fn is not None:
        meta = dict(result.get("meta") or {})
        meta["watermark"] = current_watermark
        result = {**result, "meta": meta}
    return result


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
                # 先落 job_runs(上一行),再推告警;缺代理归 proxy_unavailable
                _alert_job_failure(conn, run_id, job_name, summary,
                                   source=("proxy_unavailable" if "THORDATA_PROXY" in missing
                                           else "pipeline_step_failure"))
                return {"run_id": run_id, "status": "failed", "attempt": 1, "error_summary": summary}

            attempt = 0
            last_error = None
            while attempt < max_attempts:
                attempt += 1
                _mark_running(conn, run_id, attempt)
                try:
                    result = _execute_once(spec, timeout, conn=conn, job_name=job_name)
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
                        # 先落 job_runs(上一行),再推告警(notify 永不抛)
                        _alert_job_failure(conn, run_id, job_name, last_error)
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
    """按顺序执行任务链;某步 failed/locked 后,后续步骤记 skipped(依赖检查)。

    PIPELINE_REDESIGN_V2 P3 起,生产不再有任何定时器周期性调用 `--chain`——
    7 个新定时器各自单任务直接 `--job`,或用 group_runner 顺序执行互相独立
    的任务组,都不经过这里的级联跳过逻辑。裸 `--chain`(不带 --from)现在
    只是人工端到端/全量重跑用的手动逃生舱,`locked`(被同名任务的文件锁
    挡住)仍与 `failed` 同等处理——手动全链重跑期间若某个任务名恰好被某个
    独立定时器同时占用,后续步骤仍会记 skipped(依赖检查),这是有意的
    保守行为,不是缺陷。
    """
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
    ap.add_argument("--chain", action="store_true", help="按默认顺序执行任务链(人工全量重跑用,生产定时器不再调用)")
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
        results = run_chain(names=None, start_from=args.from_step)
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
