"""allwin-api 文件描述符采样 + 看门狗(2026-09-27;systemd timer 每分钟一次,以 root 运行)。

背景:2026-08-25 起 allwin-api 多次文件描述符耗尽(`OSError: [Errno 24] Too many open files`,
软上限 1024),每次持续几分钟到几十小时,期间 accept 失败、500 大量出现、syslog 被刷爆。风暴发生
时没有任何采样,根因至今没有证据(详见 2026-09-26 排查)。本脚本两件事:

  1. 采样:每分钟记录 API 进程 fd 数与上限、8000 端口各 TCP 状态计数、fd 目标 top 20、/readyz
     结果,追加到 <log-dir>/fd_YYYYMMDD.log(按天分文件,保留 RETENTION_DAYS 天)。
  2. 看门狗:连续 3 次 /readyz 失败,或 fd 数超过软上限的 70% → 重启 allwin-api。保护:
     COOLDOWN 15 分钟内最多重启 1 次,超过就只记录、不再重启;每次重启前把当时的采样、
     ss 输出、lsof、limits、journal 尾部另存一份快照到 <log-dir>/restart_snapshots/<UTC时间戳>/。

只用标准库 + 系统命令(systemctl / ss / lsof,缺哪个就跳过对应部分并在采样里注明),
可直接 `python3 backend/cli/fdwatch.py` 运行。所有判断逻辑是纯函数(decide),便于单测。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SERVICE = "allwin-api"
PORT = 8000
READYZ_URL = "http://127.0.0.1:8000/readyz"
LOG_DIR = "/opt/allwin/shared/logs/fd_sampling"
STATE_FILE = "/var/lib/allwin-fdwatch/state.json"

CONSECUTIVE_FAILS = 3
FD_RATIO_LIMIT = 0.70
COOLDOWN_SECONDS = 15 * 60
RETENTION_DAYS = 14
TOP_N = 20
READYZ_TIMEOUT = 5


# ── 纯逻辑 ────────────────────────────────────────────────────────────


@dataclass
class State:
    fails: int = 0
    last_restart_ts: float | None = None


@dataclass
class Decision:
    action: str                      # "none" | "restart" | "suppressed"
    reasons: list[str] = field(default_factory=list)
    fails: int = 0                   # 本轮 /readyz 连续失败计数(重置前的值,写进采样便于复盘)


def decide(
    state: State,
    *,
    readyz_ok: bool,
    fd_count: int | None,
    soft_limit: int | None,
    now: float,
    consecutive_fails: int = CONSECUTIVE_FAILS,
    fd_ratio_limit: float = FD_RATIO_LIMIT,
    cooldown: int = COOLDOWN_SECONDS,
) -> Decision:
    """更新 state 并给出决定。readyz 成功会清零连续失败计数;重启(或被抑制)之后失败计数也清零,
    避免抑制期结束的瞬间因为旧计数立刻再次触发。"""
    state.fails = 0 if readyz_ok else state.fails + 1
    seen_fails = state.fails
    reasons: list[str] = []
    if state.fails >= consecutive_fails:
        reasons.append(f"/readyz 连续 {state.fails} 次失败")
    if fd_count is not None and soft_limit and fd_count > soft_limit * fd_ratio_limit:
        reasons.append(f"fd {fd_count} 超过软上限 {soft_limit} 的 {int(fd_ratio_limit * 100)}%")
    if not reasons:
        return Decision("none", fails=seen_fails)
    recent = state.last_restart_ts is not None and (now - state.last_restart_ts) < cooldown
    state.fails = 0
    if recent:
        return Decision("suppressed", reasons, seen_fails)
    state.last_restart_ts = now
    return Decision("restart", reasons, seen_fails)


def parse_soft_limit(limits_text: str) -> int | None:
    for line in limits_text.splitlines():
        if line.startswith("Max open files"):
            parts = line.split()
            # "Max open files            1024                 524288               files"
            try:
                return int(parts[3])
            except (IndexError, ValueError):
                return None
    return None


_ENDPOINT = re.compile(r"^(?P<local>\S+?)->(?P<remote>\S+?)(?:\s+\((?P<state>[A-Z_\-]+)\))?$")


def normalize_target(name: str) -> str:
    """把 lsof 的 NAME 归一,让同类连接聚合:
    `127.0.0.1:8000->127.0.0.1:50826 (ESTABLISHED)` → `127.0.0.1:8000->127.0.0.1:* (ESTABLISHED)`;
    普通文件路径原样保留;`type=STREAM` 之类原样保留。"""
    name = name.strip()
    m = _ENDPOINT.match(name)
    if m:
        remote = re.sub(r":\d+$", ":*", m.group("remote"))
        state = f" ({m.group('state')})" if m.group("state") else ""
        return f"{m.group('local')}->{remote}{state}"
    return name


def top_targets(lsof_output: str, n: int = TOP_N) -> list[tuple[int, str]]:
    """从 `lsof -nP -p PID` 输出里只统计真正的 fd 行(FD 列以数字开头),按归一后的 NAME 聚合取前 n。"""
    lines = lsof_output.splitlines()
    counter: Counter[str] = Counter()
    for line in lines[1:]:
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        fd = parts[3]
        if not re.match(r"^\d+", fd):
            continue                  # cwd / txt / mem / rtd 不是描述符
        counter[normalize_target(parts[8])] += 1
    return [(c, name) for name, c in counter.most_common(n)]


def summarize_ss(ss_output: str, port: int) -> dict[str, int]:
    """8000 端口上(本地或远端)各 TCP 状态的连接数。"""
    states: Counter[str] = Counter()
    suffix = f":{port}"
    for line in ss_output.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 5:
            continue
        if cols[3].endswith(suffix) or cols[4].endswith(suffix):
            states[cols[0]] += 1
    return dict(states)


def prune_old(log_dir: Path, now: datetime, retention_days: int = RETENTION_DAYS) -> list[str]:
    """删除 fd_YYYYMMDD.log 中早于 retention_days 的文件(快照目录 restart_snapshots 不动)。"""
    removed = []
    cutoff = (now - timedelta(days=retention_days)).strftime("%Y%m%d")
    for p in sorted(log_dir.glob("fd_*.log")):
        m = re.fullmatch(r"fd_(\d{8})\.log", p.name)
        if m and m.group(1) < cutoff:
            p.unlink(missing_ok=True)
            removed.append(p.name)
    return removed


# ── 与系统交互 ────────────────────────────────────────────────────────


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main_pid(service: str) -> int:
    out = _run(["systemctl", "show", service, "-p", "MainPID", "--value"]).strip()
    return int(out) if out.isdigit() else 0


def service_state(service: str) -> str:
    return _run(["systemctl", "show", service, "-p", "ActiveState", "--value"]).strip()


def fd_count(pid: int) -> int | None:
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        return None


def read_limits(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/limits").read_text()
    except OSError:
        return ""


def check_readyz(url: str, timeout: int = READYZ_TIMEOUT) -> tuple[bool, str, int]:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — 固定的 127.0.0.1 地址
            code = r.status
            r.read(1024)
        return code == 200, str(code), int((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as exc:
        return False, str(exc.code), int((time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 —— 连接被拒/超时/重置都算失败
        return False, type(exc).__name__, int((time.monotonic() - t0) * 1000)


def load_state(path: Path) -> State:
    try:
        d = json.loads(path.read_text())
        return State(int(d.get("fails", 0)), d.get("last_restart_ts"))
    except (OSError, ValueError):
        return State()


def save_state(path: Path, st: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fails": st.fails, "last_restart_ts": st.last_restart_ts}))
    os.replace(tmp, path)


def build_sample(
    now: datetime, pid: int, fds: int | None, soft: int | None, ready: tuple[bool, str, int],
    ss_states: dict[str, int], top: list[tuple[int, str]], fails: int, notes: list[str],
) -> str:
    ratio = f"{fds / soft:.3f}" if fds is not None and soft else "n/a"
    lines = [
        f"=== {now.strftime('%Y-%m-%dT%H:%M:%SZ')} pid={pid} fd={fds if fds is not None else 'n/a'} "
        f"soft={soft if soft else 'n/a'} ratio={ratio} readyz={ready[1]}({ready[2]}ms) fails={fails}",
        "ss:" + str(PORT) + " " + (" ".join(f"{k}={v}" for k, v in sorted(ss_states.items())) or "(无连接)"),
    ]
    if top:
        lines.append("top fd targets:")
        lines += [f"  {c:>5}  {name}" for c, name in top]
    lines += [f"note: {n}" for n in notes]
    return "\n".join(lines) + "\n"


def save_snapshot(snap_dir: Path, sample: str, pid: int, service: str, reasons: list[str]) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "reasons.txt").write_text("\n".join(reasons) + "\n")
    (snap_dir / "sample.txt").write_text(sample)
    (snap_dir / "ss_tanp.txt").write_text(_run(["ss", "-tanp"]))
    (snap_dir / "lsof.txt").write_text(_run(["lsof", "-nP", "-p", str(pid)], timeout=30) if pid else "")
    (snap_dir / "limits.txt").write_text(read_limits(pid) if pid else "")
    (snap_dir / "journal_tail.txt").write_text(_run(["journalctl", "-u", service, "-n", "100", "--no-pager"], 30))


def run_once(
    *, service: str = SERVICE, port: int = PORT, readyz_url: str = READYZ_URL,
    log_dir: Path = Path(LOG_DIR), state_file: Path = Path(STATE_FILE),
    now: datetime | None = None, dry_run: bool = False,
    consecutive_fails: int = CONSECUTIVE_FAILS, fd_ratio_limit: float = FD_RATIO_LIMIT,
    cooldown: int = COOLDOWN_SECONDS, retention_days: int = RETENTION_DAYS,
    restart=None,
) -> Decision:
    now = now or datetime.now(timezone.utc)
    log_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    active = service_state(service)
    pid = main_pid(service) if active in ("active", "reloading") else 0
    fds = fd_count(pid) if pid else None
    soft = parse_soft_limit(read_limits(pid)) if pid else None
    if not pid:
        notes.append(f"服务 {service} 状态={active or 'unknown'},没有 MainPID")
    ready = check_readyz(readyz_url) if pid else (False, "no-pid", 0)
    ss_out = _run(["ss", "-tan"])
    ss_states = summarize_ss(ss_out, port) if ss_out else {}
    if not ss_out:
        notes.append("ss 不可用")
    lsof_out = _run(["lsof", "-nP", "-p", str(pid)], timeout=30) if pid else ""
    top = top_targets(lsof_out) if lsof_out else []
    if pid and not lsof_out:
        notes.append("lsof 不可用或无输出")

    state = load_state(state_file)
    # 服务正在启动/重启(activating)时不计入失败,避免部署窗口误判
    if active == "activating":
        decision = Decision("none", ["服务正在启动,本轮不计失败"], state.fails)
    else:
        decision = decide(
            state, readyz_ok=ready[0], fd_count=fds, soft_limit=soft, now=now.timestamp(),
            consecutive_fails=consecutive_fails, fd_ratio_limit=fd_ratio_limit, cooldown=cooldown,
        )
    sample = build_sample(now, pid, fds, soft, ready, ss_states, top, decision.fails, notes)
    if decision.action != "none":
        sample += f"DECISION: {decision.action} — {'; '.join(decision.reasons)}\n"

    day_file = log_dir / f"fd_{now.strftime('%Y%m%d')}.log"
    with open(day_file, "a", encoding="utf-8") as f:
        f.write(sample)

    if decision.action == "restart":
        snap = log_dir / "restart_snapshots" / now.strftime("%Y%m%dT%H%M%SZ")
        save_snapshot(snap, sample, pid, service, decision.reasons)
        with open(log_dir / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} RESTART {service}: {'; '.join(decision.reasons)} (snapshot {snap.name})\n")
        if not dry_run:
            (restart or (lambda s: subprocess.run(["systemctl", "restart", s], timeout=120, check=False)))(service)
    elif decision.action == "suppressed":
        with open(log_dir / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} SUPPRESSED {service}: {'; '.join(decision.reasons)} "
                    f"(15 分钟内已重启过,只记录不重启)\n")

    save_state(state_file, state)
    prune_old(log_dir, now, retention_days)
    return decision


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--service", default=SERVICE)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--readyz-url", default=READYZ_URL)
    ap.add_argument("--log-dir", default=LOG_DIR)
    ap.add_argument("--state-file", default=STATE_FILE)
    ap.add_argument("--dry-run", action="store_true", help="采样并判断,但不真的重启(仍会写快照与 watchdog.log)")
    args = ap.parse_args(argv)
    d = run_once(
        service=args.service, port=args.port, readyz_url=args.readyz_url,
        log_dir=Path(args.log_dir), state_file=Path(args.state_file), dry_run=args.dry_run,
    )
    if d.action != "none":
        print(f"fdwatch: {d.action} — {'; '.join(d.reasons)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
