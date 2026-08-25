"""方糖(Server 酱)告警通道(数据管道重建 Phase 5)。

设计纪律(合并前身项目两套实现的优点,修正其已知缺陷):
- **持久化优先、推送其次**:先落 platform.db 的 pipeline_alerts,再尝试推送;
  推送失败不影响记录。`notify()` 永不抛异常——但"永不抛"不等于"永不被发现":
  1) 落库失败打可 grep 的 `ALLWIN_ALERT_PERSIST_FAILED` 标记到 stderr;
  2) 返回值带 `persisted=False`,worker 把它记进 job_runs.meta_json;
  3) ops_check 把"pending(notify_result IS NULL)超过 1 小时"报 WARN。
- **解析响应体**:HTTP 200 不代表推送成功(40024=配额耗尽等);只有响应 JSON
  `code == 0` 才算 sent——修正前身项目只看 status_code==200 的缺陷。
- **优先级 + 每日配额**(免费版 5 条/天):软上限 4 条,超出后只放行 CRITICAL;
  WARNING ≤2 条/天、INFO ≤1 条/天;CRITICAL 不设本地上限(配额真耗尽时由
  服务端 40024 如实拒绝并记录,不在本地伪装成功)。
- **去重/冷却**:同一 dedup_key 24h 内已成功推送过 → 本条记 suppressed:dedup。
- **脱敏**:title/body 逐行过 ops_check._sanitize_summary(最终输出边界同一套
  规则);sendkey 出现在任何文本中都会被剥除;错误信息一律用
  `https://sctapi.ftqq.com/<REDACTED>.send`,绝不拼真实 key。
- 配置只在调用时读环境变量(不在 import 期读,对齐 ops_check 纪律):
  NOTIFY_ENABLED=1 才推送;SERVERCHAN_SENDKEY 缺失时记 failed:misconfigured
  (不 fail-fast——告警通道坏了不应拖垮采集本身,由 ops_check WARN 暴露)。

用法(两个汇聚点,不散调):
  worker/runner.run_job 失败分支 → notify(CRITICAL, "pipeline_step_failure", ...)
  cli/ops_check --notify / cli/pipeline_gates → 质量门与运维告警
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from backend.db.connections import connect_rw, tx
from backend.db.util import utc_now_iso

LEVELS = ("INFO", "WARNING", "CRITICAL")

# P0(CRITICAL)告警来源白名单——唯一定义处,不得在别处再抄一份
# (前身项目分裂成两份且已漂移的教训)。
P0_ALERT_SOURCES = frozenset({
    "pipeline_step_failure",
    "source_waf_blocked",
    "proxy_unavailable",
    "league_coverage_regression",
    "fixtures_window_empty",
    "entity_resolution_degraded",
    "postmatch_match_retry_exhausted",
    "season_label_drift",
    "physical_stats_poll_exhausted",
})

DEDUP_HOURS = 24
DAILY_QUOTA_DEFAULT = 5      # 方糖免费版硬上限
SOFT_QUOTA = 4               # 本地软上限:留 1 条给 CRITICAL
WARNING_DAILY_MAX = 2
INFO_DAILY_MAX = 1

PERSIST_FAIL_MARKER = "ALLWIN_ALERT_PERSIST_FAILED"
REDACTED_ENDPOINT = "https://sctapi.ftqq.com/<REDACTED>.send"
_PUSH_TIMEOUT_SECONDS = 10


def _enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("NOTIFY_ENABLED") or "").strip() == "1"


def _sendkey(env=None) -> str | None:
    env = os.environ if env is None else env
    key = (env.get("SERVERCHAN_SENDKEY") or "").strip()
    return key or None


def _daily_quota(env=None) -> int:
    env = os.environ if env is None else env
    raw = (env.get("NOTIFY_DAILY_QUOTA") or "").strip()
    try:
        val = int(raw) if raw else DAILY_QUOTA_DEFAULT
    except ValueError:
        val = DAILY_QUOTA_DEFAULT
    return val if val > 0 else DAILY_QUOTA_DEFAULT


def _sanitize_text(text: str, sendkey: str | None) -> str:
    """逐行过 ops_check 的最终输出边界脱敏,并剥除 sendkey 本身。

    延迟 import 避免 notify ↔ ops_check 循环依赖(ops_check --notify 会反向
    import 本模块)。逐行处理:_sanitize_summary 命中不安全形状时整行退化为
    占位符,不会因一行命中而毁掉整个 body。
    """
    from backend.cli.ops_check import _sanitize_summary

    if not text:
        return ""
    if sendkey:
        text = text.replace(sendkey, "<REDACTED>")
    lines = []
    for line in str(text).splitlines():
        cleaned = _sanitize_summary(line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _push_serverchan(sendkey: str, title: str, body: str) -> tuple[bool, str]:
    """真实推送。返回 (是否 sent, 结果串)。任何异常只回传类别名,绝不含 URL/key。"""
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = urllib.parse.urlencode({"title": title[:80], "desp": body[:8000]}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_PUSH_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return False, f"failed:http_{exc.code}"
    except Exception as exc:  # noqa: BLE001 — 推送边界:网络异常不外抛、不带 URL
        return False, f"failed:exception_{type(exc).__name__}"
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return False, "failed:bad_response"
    code = payload.get("code")
    if code == 0:
        return True, "sent"
    return False, f"failed:api_code_{code}"


def _record(conn, level, source, dedup_key, title, body, now_iso) -> int | None:
    """落一行 pipeline_alerts;失败打 stderr 标记并返回 None(暴露路径 ①)。"""
    try:
        with tx(conn):
            cur = conn.execute(
                """INSERT INTO pipeline_alerts
                   (level, source, dedup_key, title, body, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (level, source, dedup_key, title, body, now_iso),
            )
        return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 — 告警器自身故障不能变成任务故障
        print(f"{PERSIST_FAIL_MARKER} source={source} level={level} "
              f"err={type(exc).__name__}", file=sys.stderr)
        return None


def _finish_alert(conn, alert_id, result, sent, now_iso) -> None:
    if alert_id is None:
        return
    try:
        with tx(conn):
            conn.execute(
                "UPDATE pipeline_alerts SET notify_result=?, notified_at=? WHERE id=?",
                (result, now_iso if sent else None, alert_id),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"{PERSIST_FAIL_MARKER} stage=finish alert_id={alert_id} "
              f"err={type(exc).__name__}", file=sys.stderr)


def _sent_counts_today(conn, now_iso: str) -> dict:
    """当日(UTC 自然日)已成功推送计数:总数 + 分级别。"""
    day_start = now_iso[:10] + "T00:00:00Z"
    rows = conn.execute(
        """SELECT level, COUNT(*) AS n FROM pipeline_alerts
           WHERE notify_result='sent' AND created_at >= ? GROUP BY level""",
        (day_start,),
    ).fetchall()
    by_level = {r["level"]: r["n"] for r in rows}
    return {"total": sum(by_level.values()), "by_level": by_level}


def _dedup_hit(conn, dedup_key: str, now_iso: str) -> bool:
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.fromisoformat(now_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
              - timedelta(hours=DEDUP_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """SELECT 1 FROM pipeline_alerts
           WHERE dedup_key=? AND notify_result='sent' AND created_at >= ? LIMIT 1""",
        (dedup_key, cutoff),
    ).fetchone()
    return row is not None


def notify(
    level: str,
    source: str,
    title: str,
    body: str = "",
    dedup_key: str | None = None,
    conn=None,
    now_iso: str | None = None,
    env: dict | None = None,
) -> dict:
    """记录并(按配额/去重/开关)推送一条告警。永不抛异常。

    返回 {"persisted", "notified", "result", "alert_id"};
    result ∈ sent / suppressed:disabled|dedup|quota / failed:misconfigured|http_*|
    api_code_*|bad_response|exception_*|internal。
    """
    out = {"persisted": False, "notified": False, "result": None, "alert_id": None}
    own_conn = conn is None
    try:
        now = now_iso or utc_now_iso()
        if level not in LEVELS:
            level = "CRITICAL"   # 未知级别向上取:宁可占配额,不可静默降级
        sendkey = _sendkey(env)
        title_s = _sanitize_text(title, sendkey) or "(untitled)"
        body_s = _sanitize_text(body, sendkey)
        key = dedup_key or f"{source}:{title_s}"

        if own_conn:
            conn = connect_rw("platform")
        try:
            alert_id = _record(conn, level, source, key, title_s, body_s, now)
            out["persisted"] = alert_id is not None
            out["alert_id"] = alert_id

            # 结局判定顺序:开关 → 凭证 → 去重 → 配额 → 推送
            if not _enabled(env):
                result, sent = "suppressed:disabled", False
            elif sendkey is None:
                result, sent = "failed:misconfigured", False
            elif out["persisted"] and _dedup_hit(conn, key, now):
                result, sent = "suppressed:dedup", False
            else:
                counts = _sent_counts_today(conn, now) if out["persisted"] else \
                    {"total": 0, "by_level": {}}
                quota = _daily_quota(env)
                by = counts["by_level"]
                if level == "INFO" and by.get("INFO", 0) >= INFO_DAILY_MAX:
                    result, sent = "suppressed:quota", False
                elif level == "WARNING" and by.get("WARNING", 0) >= WARNING_DAILY_MAX:
                    result, sent = "suppressed:quota", False
                elif level != "CRITICAL" and counts["total"] >= min(SOFT_QUOTA, quota):
                    result, sent = "suppressed:quota", False
                else:
                    # CRITICAL 超过硬配额也照发:让服务端 40024 如实说"配额耗尽",
                    # 而不是本地静默吞掉最后一条最重要的告警。
                    sent, result = _push_serverchan(sendkey, f"[{level}] {title_s}", body_s)

            _finish_alert(conn, alert_id, result, sent, now)
            out["result"] = result
            out["notified"] = sent
            return out
        finally:
            if own_conn:
                conn.close()
    except Exception as exc:  # noqa: BLE001 — notify 永不抛(暴露路径见模块 docstring)
        print(f"{PERSIST_FAIL_MARKER} stage=notify source={source} "
              f"err={type(exc).__name__}", file=sys.stderr)
        out["result"] = "failed:internal"
        return out
