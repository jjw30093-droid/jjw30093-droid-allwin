"""只读运维检查:三库可读性/migration 状态/备份新鲜度/磁盘/job_runs/source_health/告警通道。

用法:
  python -m backend.cli.ops_check            # 面向 journalctl 的文本输出
  python -m backend.cli.ops_check --json      # 结构化 JSON(字段稳定)
  python -m backend.cli.ops_check --json --notify   # WARN/CRITICAL 时经方糖推送摘要
                                                    # (allwin-opscheck.timer 用这个)

--notify 是"纯只读"的唯一例外:检查本身仍不修改任何业务数据,但推送会向
platform.db 的 pipeline_alerts 落一行告警记录(backend/notify 的持久化优先
纪律)。推送内容全部经本模块 _sanitize_summary 脱敏,不含凭证/路径/SQL。

退出码(与 systemd 的常见约定一致,具体名称见 docs/deployment-aws-cloudflare.md):
  0  OK        全部检查项健康
  1  WARN      降级但服务仍可用(如备份过期、某数据源近期失败、任务卡住)
  2  CRITICAL  需要立即关注(数据库不可读/损坏、migration pending/checksum 漂移、
               或 OPS_* 阈值配置非法——后者不执行任何数据库/磁盘检查就直接失败)

配置(生产可靠性收口第二轮):
- OPS_* 环境变量未设置或为空白 → 使用 .env.example 里的文档默认值;
- 显式设置但不合法(非数字、非 finite、超出合法范围、正整数字段给了 0/负数/
  小数)→ 立即 fail-fast(ConfigError → CLI 非零退出、stderr 打印能定位到
  具体变量名的简短消息),绝不静默改用默认值继续检查——静默回退等于允许一个
  写错的阈值悄悄关掉真实告警(例如 OPS_DISK_CRITICAL_PCT=200 会让磁盘再满
  也不报 CRITICAL)。
- 同一次 run_all_checks() 只解析一次 OpsConfig,并把这同一份配置传给
  check_backup/check_disk/check_job_runs/check_source_health,不允许各检查
  项各自重新读环境变量(避免同一次运行里出现前后不一致的阈值)。
- 校验只在显式调用 OpsConfig.from_env() 时发生(main()/run_all_checks() 均无
  参数时才会触发);模块 import 阶段不读取、不校验任何环境变量,避免一个测试
  进程里残留的非法 OPS_* 环境变量导致 import 本模块就失败、拖垮测试收集。

设计原则:
- 本工具是纯只读检查,不修改任何数据库、不触发采集或备份;
- 外部数据源(NowGoal/FotMob)健康状况独立于 API 本身——本工具的检查结果不
  影响、也不依赖 /readyz(那里只管"数据库可读 + migration 无 pending",职责
  更窄,详见 backend/api/app.py);
- error_summary 来自各 worker 自行记录,不能假设它已经安全——本工具是最终
  输出边界,必须自行脱敏(见 _sanitize_summary),不只是截断;
- 所有时间比较统一用 tz-aware UTC;
- 全部阈值可由环境变量覆盖(默认值见 .env.example)。
"""

import argparse
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.db import migrate
from backend.db.connections import connect_ro
from backend.db.paths import data_dir

OK, WARN, CRITICAL = 0, 1, 2
_LEVEL_NAME = {OK: "OK", WARN: "WARN", CRITICAL: "CRITICAL"}

_BACKUP_TS_RE = re.compile(r"^\d{8}T\d{6}Z$")
_SUMMARY_MAX = 200

# ── 默认阈值(与 .env.example 的 OPS_* 默认值保持一致;不在 import 时读环境) ──
DISK_WARN_PCT = 70.0
DISK_CRITICAL_PCT = 85.0
BACKUP_STALE_HOURS = 30
JOB_STUCK_MINUTES = 120
JOB_STALE_HOURS = 24
SOURCE_STALE_HOURS = 6


class ConfigError(Exception):
    """OPS_* 环境变量显式设置但不合法(非数字/非 finite/超出合法范围)。"""


def _parse_finite_float(env, name: str, default: float) -> float:
    """解析失败时的 ConfigError 消息只报变量名和规则,绝不回显 raw 原始值——
    环境变量本该是无害的数字阈值,但如果有人误把 Secret 填进这个变量,不应该
    因为一次配置校验失败就让它原样进日志/journal。"""
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} 不是合法有限数字") from None
    if not math.isfinite(val):
        raise ConfigError(f"{name} 必须是合法有限数字(拒绝 NaN/Infinity)")
    return val


def _parse_positive_int(env, name: str, default: int) -> int:
    """同 _parse_finite_float:错误消息不回显原始环境变量值。"""
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    raw_stripped = raw.strip()
    try:
        val = int(raw_stripped)
    except ValueError:
        raise ConfigError(f"{name} 必须是严格正整数") from None
    if val <= 0:
        raise ConfigError(f"{name} 必须是严格正整数(>0)")
    return val


@dataclass(frozen=True)
class OpsConfig:
    """一次 run_all_checks() 生命周期内共用的已验证阈值配置。"""

    disk_warn_pct: float = DISK_WARN_PCT
    disk_critical_pct: float = DISK_CRITICAL_PCT
    backup_stale_hours: int = BACKUP_STALE_HOURS
    job_stuck_minutes: int = JOB_STUCK_MINUTES
    job_stale_hours: int = JOB_STALE_HOURS
    source_stale_hours: int = SOURCE_STALE_HOURS

    @classmethod
    def from_env(cls, env: dict | None = None) -> "OpsConfig":
        """解析并严格校验 OPS_* 环境变量;任何一项非法立即抛 ConfigError,
        不返回部分校验过的配置,调用方不应该在捕获异常后继续使用默认值兜底。"""
        env = os.environ if env is None else env
        disk_warn = _parse_finite_float(env, "OPS_DISK_WARN_PCT", DISK_WARN_PCT)
        disk_critical = _parse_finite_float(env, "OPS_DISK_CRITICAL_PCT", DISK_CRITICAL_PCT)
        if not (0 < disk_warn < disk_critical <= 100):
            # 不回显解析出的具体数值——即使这里的值已经是合法数字而非任意
            # 字符串,也统一只报变量名和规则,消息格式与其它 ConfigError 一致。
            raise ConfigError(
                "磁盘阈值必须满足 0 < OPS_DISK_WARN_PCT < OPS_DISK_CRITICAL_PCT <= 100"
            )
        return cls(
            disk_warn_pct=disk_warn,
            disk_critical_pct=disk_critical,
            backup_stale_hours=_parse_positive_int(env, "OPS_BACKUP_STALE_HOURS", BACKUP_STALE_HOURS),
            job_stuck_minutes=_parse_positive_int(env, "OPS_JOB_STUCK_MINUTES", JOB_STUCK_MINUTES),
            job_stale_hours=_parse_positive_int(env, "OPS_JOB_STALE_HOURS", JOB_STALE_HOURS),
            source_stale_hours=_parse_positive_int(env, "OPS_SOURCE_STALE_HOURS", SOURCE_STALE_HOURS),
        )


_DEFAULT_CONFIG = OpsConfig()


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")

# 安全优先的整体退化策略(不是局部替换):局部替换天然假设"能猜到secret从哪
# 到哪结束"——引号、JSON、路径里的空格都会让这个假设失败(已被真实攻击样例
# 证伪:Basic 之后的完整 base64、带空格的 quoted value、JSON key、带空格的
# 绝对路径,局部替换都只删了半截)。这里改为:一旦确认某一类内容不安全,直接
# 把整条 text 换成稳定占位符,不再尝试保留"看起来安全的其余部分"。
#
# 检测顺序即优先级(仅影响命中时具体用哪个占位符,不影响安全性——任何一类
# 命中,原文都会被整体清除):SQL > Traceback > Auth > Credential > 敏感
# key=value > 绝对路径。
_SQL_RE = re.compile(
    r"(?i)\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|PRAGMA"
    r"|WITH|REPLACE|ATTACH|DETACH|VACUUM)\b"
)
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r"|File \"[^\"]*\", line \d+"
    r"|\b\w*(?:Error|Exception)\b\s*:"
)
# Authorization/Bearer/Basic:不管后面跟的是 token 还是完整 Base64 凭证,
# 一旦确认是认证头形状,整条清除——不尝试只删除 scheme 名词(Basic/Bearer)
# 而放过紧跟其后的真正凭证。
_AUTH_RE = re.compile(r"(?i)\bauthorization\b\s*[:=]|\bbearer\b|\bbasic\s+\S")
# URL userinfo(scheme://user:pass@host)与 proxy=user:pass@host。
# 内联 flag (?i) 必须放在整个 pattern 最前面(Python 3.11+ 对"非开头位置的
# 全局 inline flag"直接报 PatternError,不能分别放在每个分支里)。
_CREDENTIAL_RE = re.compile(
    r"(?i)://[^/\s@]+:[^/\s@]+@|\bproxy\s*=\s*[^:\s@]+:[^:\s@]+@"
)
# 敏感 key=value:只检测"是否存在这个形状",不试图捕获/替换 value 本身——
# 这样 value 带不带引号、是否 JSON 格式、内部有没有空格都不影响判定,天然
# 兼容 password=x / password: x / password="a b" / "password":"x" /
# 'password': 'x' 等任意写法。
_SECRET_KEYS = r"(?:password|passwd|pass|pwd|token|secret|api[_-]?key|apikey|authorization|user(?:name)?)"
_SECRET_KEY_RE = re.compile(r"(?i)['\"]?\b" + _SECRET_KEYS + r"\b['\"]?\s*[:=]")
# 绝对路径:同样只检测"文本里是否出现这个前缀",不试图猜路径在哪结束
# (路径本身可能带空格,如 "/Users/x/My Secret Folder/file.db")。
_UNIX_PATH_RE = re.compile(r"/(?:Users|home|root|opt|srv|var|etc|tmp)(?:/|\b)")
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[A-Za-z]:\\")


def _sanitize_summary(text) -> str | None:
    """error_summary 是各 worker 自行记录的自由文本,不能假设它已经安全——本函数
    是最终输出边界,顺序严格:(1) 清成单行、去控制字符 (2) 检测各类不安全形状,
    命中即整体退化为稳定占位符(3) 都不命中时,才对剩余安全文本做长度截断
    (脱敏必须在截断之前,否则可能截出半个 Secret)。

    刻意不做"局部替换 + 保留其余文本"——那需要准确判断 secret 从哪到哪结束,
    引号/JSON/带空格的路径都会让这个判断失败。安全优先:一旦确认某段内容
    命中已知不安全模式,整条替换,不追求保留额外诊断信息。
    """
    if not text:
        return None
    text = _CONTROL_CHARS_RE.sub(" ", str(text)).strip()
    if not text:
        return None

    if _SQL_RE.search(text):
        return "[SQL_REDACTED]"
    if _TRACEBACK_RE.search(text):
        return "[TRACEBACK_REDACTED]"
    if _AUTH_RE.search(text):
        return "[AUTH_REDACTED]"
    if _CREDENTIAL_RE.search(text):
        return "[CREDENTIAL_REDACTED]"
    if _SECRET_KEY_RE.search(text):
        return "[SECRET_REDACTED]"
    if _UNIX_PATH_RE.search(text) or _WINDOWS_PATH_RE.search(text):
        return "[PATH_REDACTED]"

    return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 3] + "..."


def check_databases() -> list[dict]:
    """三库存在、可只读打开、PRAGMA quick_check、migration 无 pending/checksum drift。"""
    results = []
    for name in ("core", "platform", "odds"):
        entry = {"name": name, "level": OK, "detail": "ok"}
        try:
            conn = connect_ro(name)
        except Exception:
            entry.update(level=CRITICAL, detail="unreadable_or_missing")
            results.append(entry)
            continue
        try:
            row = conn.execute("PRAGMA quick_check;").fetchone()
            quick = row[0] if row else "unknown"
        except Exception:
            entry.update(level=CRITICAL, detail="quick_check_error")
            results.append(entry)
            continue
        finally:
            conn.close()
        if quick != "ok":
            entry.update(level=CRITICAL, detail="quick_check_failed")
            results.append(entry)
            continue
        try:
            st = migrate.status(name)
        except Exception:
            entry.update(level=CRITICAL, detail="migration_status_error")
            results.append(entry)
            continue
        if st["pending"]:
            entry.update(level=CRITICAL, detail="pending_migrations",
                         pending_count=len(st["pending"]))
        elif st["checksum_drift"]:
            entry.update(level=CRITICAL, detail="checksum_drift",
                         drift_count=len(st["checksum_drift"]))
        results.append(entry)
    return results


def check_backup(config: OpsConfig | None = None) -> dict:
    """最近一份"完整"(backup_metadata.json 存在且 complete=true)本地备份的新鲜度。
    只看名字带合法 UTC 时间戳、且明确标记为完整的目录——不完整/无 metadata 的
    目录不计入"最近备份",与 restore_verify.sh 的判定标准保持一致。"""
    config = config or _DEFAULT_CONFIG
    backups_root = data_dir() / "backups"
    if not backups_root.exists():
        return {"level": WARN, "detail": "no_backups_directory"}
    try:
        candidates = sorted(
            (p.name for p in backups_root.iterdir() if p.is_dir() and _BACKUP_TS_RE.match(p.name)),
            reverse=True,
        )
    except OSError:
        return {"level": WARN, "detail": "backups_directory_unreadable"}

    for name in candidates:
        meta_path = backups_root / name / "backup_metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not meta.get("complete"):
            continue
        try:
            created = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return {"level": WARN, "detail": "latest_backup_timestamp_unparseable"}
        age_hours = (_now() - created).total_seconds() / 3600
        if age_hours > config.backup_stale_hours:
            return {"level": WARN, "detail": "stale", "age_hours": round(age_hours, 1)}
        return {"level": OK, "detail": "fresh", "age_hours": round(age_hours, 1)}
    return {"level": WARN, "detail": "no_complete_backup_found"}


def check_disk(config: OpsConfig | None = None) -> dict:
    config = config or _DEFAULT_CONFIG
    try:
        usage = shutil.disk_usage(data_dir())
    except OSError:
        return {"level": WARN, "detail": "disk_usage_unavailable"}
    pct = (usage.used / usage.total * 100) if usage.total else 0.0
    level = OK
    if pct >= config.disk_critical_pct:
        level = CRITICAL
    elif pct >= config.disk_warn_pct:
        level = WARN
    return {"level": level, "used_pct": round(pct, 1),
            "warn_pct": config.disk_warn_pct, "critical_pct": config.disk_critical_pct}


def check_job_runs(config: OpsConfig | None = None) -> dict:
    """每个已出现过的任务名的最近一次运行:running 超过阈值 → stuck;
    failed → warn;记录最后一次成功的时间是否过期。

    查询本身也包在 try/except 里(不只是 connect_ro 那一步)——platform.db 损坏
    到"能连接但一查询就报错"的程度时,check_databases() 已经会把它标成
    CRITICAL,这里只需要优雅降级、不崩溃、不让一个库的损坏拖垮其他检查项的
    汇报,不需要重复诊断。
    """
    config = config or _DEFAULT_CONFIG
    try:
        conn = connect_ro("platform")
    except Exception:
        return {"level": WARN, "detail": "platform_db_unavailable", "jobs": []}
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_runs'"
        ).fetchone()
        if not has_table:
            return {"level": OK, "detail": "no_job_runs_table_yet", "jobs": []}

        names = [r[0] for r in conn.execute("SELECT DISTINCT job_name FROM job_runs")]
        overall = OK
        entries = []
        for name in names:
            last = conn.execute(
                "SELECT status, started_at, finished_at FROM job_runs"
                " WHERE job_name=? ORDER BY created_at DESC LIMIT 1", (name,)
            ).fetchone()
            last_success = conn.execute(
                "SELECT finished_at FROM job_runs WHERE job_name=? AND status='succeeded'"
                " ORDER BY finished_at DESC LIMIT 1", (name,)
            ).fetchone()

            entry = {"job": name, "last_status": last["status"] if last else None}
            level = OK

            if last and last["status"] == "running":
                started = _parse_iso(last["started_at"])
                if started is not None:
                    running_minutes = (_now() - started).total_seconds() / 60
                    if running_minutes > config.job_stuck_minutes:
                        level = WARN
                        entry["detail"] = "stuck_running"
                        entry["running_minutes"] = round(running_minutes, 1)
            elif last and last["status"] == "failed":
                level = WARN
                entry["detail"] = "last_run_failed"

            if last_success is None:
                entry["last_success_at"] = None
                entry["never_succeeded"] = True
                # 第一次运行且仍在进行中时不算异常——等它跑完(succeeded/failed)
                # 再判断"从未成功过"是否成立,不然任何任务刚起步就会被误报 WARN。
                if last and last["status"] != "running":
                    level = max(level, WARN)
            else:
                last_dt = _parse_iso(last_success["finished_at"])
                entry["last_success_at"] = last_success["finished_at"]
                if last_dt is not None:
                    stale_hours = (_now() - last_dt).total_seconds() / 3600
                    entry["hours_since_last_success"] = round(stale_hours, 1)
                    if stale_hours > config.job_stale_hours:
                        level = max(level, WARN)
                        entry.setdefault("detail", "stale_last_success")

            entry["level"] = level
            overall = max(overall, level)
            entries.append(entry)
        return {"level": overall, "jobs": entries}
    except Exception:
        return {"level": WARN, "detail": "platform_db_query_error", "jobs": []}
    finally:
        conn.close()


def check_source_health(config: OpsConfig | None = None) -> dict:
    """odds.db 的 source_health:区分"从未成功过"与"曾经成功但现在过期"。

    查询本身也包在 try/except 里,原因同 check_job_runs:odds.db 损坏时
    check_databases() 已经会报 CRITICAL,这里只需优雅降级、不崩溃。
    """
    config = config or _DEFAULT_CONFIG
    try:
        conn = connect_ro("odds")
    except Exception:
        return {"level": WARN, "detail": "odds_db_unavailable", "sources": []}
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_health'"
        ).fetchone()
        if not has_table:
            return {"level": OK, "detail": "no_source_health_table_yet", "sources": []}

        sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM source_health")]
        overall = OK
        entries = []
        for src in sources:
            last_ok = conn.execute(
                "SELECT checked_at FROM source_health WHERE source=? AND ok=1"
                " ORDER BY checked_at DESC LIMIT 1", (src,)
            ).fetchone()
            last_any = conn.execute(
                "SELECT checked_at, ok, error_summary FROM source_health WHERE source=?"
                " ORDER BY checked_at DESC LIMIT 1", (src,)
            ).fetchone()

            entry = {"source": src}
            if last_ok is None:
                entry.update(level=WARN, detail="never_succeeded")
                overall = max(overall, WARN)
            else:
                last_dt = _parse_iso(last_ok["checked_at"])
                if last_dt is not None:
                    age_hours = (_now() - last_dt).total_seconds() / 3600
                    entry["hours_since_last_success"] = round(age_hours, 1)
                    if age_hours > config.source_stale_hours:
                        entry.update(level=WARN, detail="stale")
                        overall = max(overall, WARN)
                    else:
                        entry.update(level=OK, detail="recent")
                else:
                    entry.update(level=OK, detail="recent")
            if last_any is not None and not last_any["ok"]:
                entry["last_failure_summary"] = _sanitize_summary(last_any["error_summary"])
            entries.append(entry)
        return {"level": overall, "sources": entries}
    except Exception:
        return {"level": WARN, "detail": "odds_db_query_error", "sources": []}
    finally:
        conn.close()


ALERT_PENDING_WARN_MINUTES = 60


def check_pipeline_alerts(config: OpsConfig | None = None) -> dict:
    """告警通道自检("永不抛异常"不能变成"永不被发现"的第三条暴露路径):
    - pending(已落库但 notify_result 仍为 NULL)超过 1 小时 → WARN:说明推送
      流程中途死掉或告警器自身故障;
    - 近 24h 出现 failed:misconfigured → WARN:启用了推送却没配 SERVERCHAN_SENDKEY。
    表尚未迁移出来时如实报告,不视为异常(与其它 check_* 的降级风格一致)。
    """
    _ = config  # 本检查目前使用固定阈值(pending 1 小时),保留签名一致性
    try:
        conn = connect_ro("platform")
    except Exception:
        return {"level": WARN, "detail": "platform_db_unavailable"}
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_alerts'"
        ).fetchone()
        if not has_table:
            return {"level": OK, "detail": "no_pipeline_alerts_table_yet"}
        cutoff = (_now() - timedelta(minutes=ALERT_PENDING_WARN_MINUTES)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        pending = conn.execute(
            "SELECT COUNT(*) FROM pipeline_alerts WHERE notify_result IS NULL"
            " AND created_at < ?", (cutoff,),
        ).fetchone()[0]
        day_ago = (_now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        misconfigured = conn.execute(
            "SELECT COUNT(*) FROM pipeline_alerts"
            " WHERE notify_result='failed:misconfigured' AND created_at >= ?", (day_ago,),
        ).fetchone()[0]
        level = OK
        entry = {"pending_over_1h": pending, "misconfigured_24h": misconfigured}
        if pending:
            level = WARN
            entry["detail"] = "alerts_pending_over_1h"
        elif misconfigured:
            level = WARN
            entry["detail"] = "notify_misconfigured"
        entry["level"] = level
        return entry
    except Exception:
        return {"level": WARN, "detail": "platform_db_query_error"}
    finally:
        conn.close()


def run_all_checks(config: OpsConfig | None = None) -> dict:
    """resolve 一次配置(未显式传入则从环境变量解析,非法立即抛 ConfigError),
    同一份配置传给全部依赖阈值的检查项,避免同一次运行内前后阈值不一致。"""
    if config is None:
        config = OpsConfig.from_env()
    db_results = check_databases()
    backup_result = check_backup(config)
    disk_result = check_disk(config)
    jobs_result = check_job_runs(config)
    source_result = check_source_health(config)
    alerts_result = check_pipeline_alerts(config)

    overall = max(
        [OK]
        + [r["level"] for r in db_results]
        + [backup_result["level"], disk_result["level"], jobs_result["level"],
           source_result["level"], alerts_result["level"]]
    )
    return {
        "level": overall,
        "level_name": _LEVEL_NAME[overall],
        "checked_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "databases": db_results,
        "backup": backup_result,
        "disk": disk_result,
        "job_runs": jobs_result,
        "source_health": source_result,
        "alerts": alerts_result,
    }


def _print_text(report: dict) -> None:
    print(f"allwin ops_check: {report['level_name']} @ {report['checked_at']}")
    for db in report["databases"]:
        print(f"  db.{db['name']}: {_LEVEL_NAME[db['level']]} ({db['detail']})")
    b = report["backup"]
    print(f"  backup: {_LEVEL_NAME[b['level']]} ({b['detail']})"
          + (f" age={b['age_hours']}h" if "age_hours" in b else ""))
    d = report["disk"]
    print(f"  disk: {_LEVEL_NAME[d['level']]} used={d.get('used_pct', '?')}%")
    j = report["job_runs"]
    print(f"  job_runs: {_LEVEL_NAME[j['level']]} ({len(j['jobs'])} jobs tracked)")
    for job in j["jobs"]:
        if job["level"] != OK:
            print(f"    - {job['job']}: {job.get('detail', job['last_status'])}")
    s = report["source_health"]
    print(f"  source_health: {_LEVEL_NAME[s['level']]} ({len(s['sources'])} sources tracked)")
    for src in s["sources"]:
        if src["level"] != OK:
            print(f"    - {src['source']}: {src.get('detail')}")
    a = report.get("alerts", {})
    if a:
        print(f"  alerts: {_LEVEL_NAME[a['level']]} ({a.get('detail', 'ok')})")


def _problem_components(report: dict) -> list[str]:
    """非 OK 的检查项名(用于 dedup_key 与推送正文,全部为本模块生成的稳定标识)。"""
    problems = []
    for db in report["databases"]:
        if db["level"] != OK:
            problems.append(f"db.{db['name']}:{db['detail']}")
    for key in ("backup", "disk", "alerts"):
        entry = report.get(key) or {}
        if entry.get("level", OK) != OK:
            problems.append(f"{key}:{entry.get('detail', entry.get('used_pct', ''))}")
    for job in report["job_runs"].get("jobs", []):
        if job["level"] != OK:
            problems.append(f"job.{job['job']}:{job.get('detail', job.get('last_status'))}")
    for src in report["source_health"].get("sources", []):
        if src["level"] != OK:
            problems.append(f"source.{src['source']}:{src.get('detail')}")
    if report["job_runs"].get("level", OK) != OK and not report["job_runs"].get("jobs"):
        problems.append(f"job_runs:{report['job_runs'].get('detail')}")
    if report["source_health"].get("level", OK) != OK and not report["source_health"].get("sources"):
        problems.append(f"source_health:{report['source_health'].get('detail')}")
    return problems


def _notify_report(report: dict) -> dict:
    """WARN/CRITICAL 时经方糖推送摘要(推送前所有自由文本已由各 check 项
    _sanitize_summary 脱敏;这里只拼装本模块生成的稳定标识)。"""
    from backend import notify as notify_mod

    problems = _problem_components(report)
    level = "CRITICAL" if report["level"] >= CRITICAL else "WARNING"
    dedup = f"ops_check:{report['level_name']}:" + ",".join(sorted(problems)[:8])
    return notify_mod.notify(
        level=level, source="ops_check",
        title=f"ops_check {report['level_name']}",
        body="\n".join(problems) or "(无明细)",
        dedup_key=dedup,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="allwin 只读运维检查(--notify 为唯一写例外:落告警记录并推送)")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON(字段稳定)而不是文本")
    ap.add_argument("--notify", action="store_true",
                    help="WARN/CRITICAL 时经方糖推送摘要(先落 pipeline_alerts 再推)")
    args = ap.parse_args(argv)

    # 配置校验先于任何数据库/磁盘检查:非法阈值必须 fail-fast,不静默改用默认值
    # 继续跑完检查——那样会让写错的阈值悄悄关掉真实告警。
    try:
        config = OpsConfig.from_env()
    except ConfigError as exc:
        print(f"ops_check: 配置错误,拒绝执行: {exc}", file=sys.stderr)
        return CRITICAL

    report = run_all_checks(config)
    if args.notify and report["level"] > OK:
        report["notify"] = _notify_report(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return report["level"]


if __name__ == "__main__":
    sys.exit(main())
