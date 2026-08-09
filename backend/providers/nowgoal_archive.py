"""NowGoal season-archive 历史抓取(curl_cffi + 住宅代理)+ 纯解析。

2026-08-08 新增,给 `backend/cli/ingest_nowgoal_season_odds.py` 用,回补新接入
联赛(挪超 59 / 瑞典超 67)已完赛比赛的赔率两点摘要。

与 `backend/providers/nowgoal.py` 的关系:
- 纯函数 `looks_blocked` / `normalize_for_inversion` / `WAFBlockedError` 直接
  从那里 import 复用(它们不发网络请求,和网络层无关);
- 网络层完全独立:`nowgoal.py` 的 `_http_get` 是裸 httpx、不走代理,是**生产
  实时轮询**(`backend/cli/poll_nowgoal.py`,systemd/launchd 已在跑)的路径,
  本模块一行都不碰、不import、不修改。

时区纪律(2026-08-08 实测确认,关键且容易踩坑):
- `nowgoal.py` 的 `type=6` 日程 A 行 kickoff **本身就是 UTC**
  (2026-07-21 三场真实交叉验证,kickoff_diff_seconds=0),不做转换。
- 本模块的 season-archive `ScheduleList` 里的 kickoff 是**北京墙上时间**
  (UTC+8)。用 J1/K1 历史回填产物 × `dim_match.kickoff_at_utc` 实测 8 例,
  恒定 -8h,零反例。`archive_kickoff_to_utc()` 是这个转换的唯一实现,
  两种来源的行绝不可混用同一套时区假设。

赛季键格式(2026-08-08 实测确认,与最初假设不同)：
跨年联赛(如 top5)用 "YYYY-YYYY"(如 "2020-2021");**自然年联赛
(挪超/瑞典超/J1/K1)用裸年份**(如 "2026"),不是 "2026-2026"。
真实探测:`s22_en.json`(挪超)在 season="2026" 时返回
`LeagueInfo=[22,"Norway Eliteserien","2026",...]`;season="2026-2026" 时
返回一个 24339 字节的通用 HTML 错误页(同一页面,任何联赛/赛季组合都一样),
不是有效 JSON。

NowGoal 内部联赛 id(2026-08-08 双重确证,不是猜测):
- 挪超(Eliteserien,FotMob 59)= 22:`/oddscomp/<已知真实 titan>` 页面里
  `sclassId: parseInt('22')`,且与已采集的 `type=6` 联赛目录
  `B[7]=[22,'NOR D1','Norway Eliteserien',...]` 一致。
- 瑞典超(Allsvenskan,FotMob 67)= 26:同样双重确证
  (`sclassId: parseInt('26')`,`B[8]=[26,'SWE D1','Swedish Allsvenskan',...]`)。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

from backend.providers.nowgoal import WAFBlockedError, looks_blocked, normalize_for_inversion

log = logging.getLogger(__name__)

BASE_ARCHIVE = "https://football.nowgoal26.com"
BASE_LIVE = "https://live10.nowgoal26.com"

MIX_CID_BET365, EURO_CID_BET365 = "8", "281"
MIX_CID_MACAUSLOT, EURO_CID_MACAUSLOT = "1", "80"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

_SHANGHAI = timezone(timedelta(hours=8))

_DEFAULT_PROXY = object()  # sentinel: 未显式传 proxy 时走 env 解析


class NowGoalArchiveError(Exception):
    """本模块的抓取/解析失败,消息不含外部响应原文。"""


class NowGoalArchiveTransportError(NowGoalArchiveError):
    pass


class NowGoalArchiveHTTPError(NowGoalArchiveError):
    pass


class SeasonIdentityError(NowGoalArchiveError):
    """archive_season 的自证身份(LeagueInfo[0]/[2])与请求参数不符,拒绝使用。"""


def archive_kickoff_to_utc(local: str) -> str:
    """season-archive 的北京墙上时间 -> UTC ISO8601(唯一实现,见模块 docstring)。

    输入形如 "2026-04-07 17:00" 或 "2026-04-07 17:00:00"。
    """
    local = local.strip()
    fmt = "%Y-%m-%d %H:%M:%S" if local.count(":") == 2 else "%Y-%m-%d %H:%M"
    dt_local = datetime.strptime(local, fmt).replace(tzinfo=_SHANGHAI)
    dt_utc = dt_local.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


class NowGoalArchiveTransport:
    """curl_cffi + THORDATA_PROXY 住宅轮换代理,与 FotMobClient 同一策略。

    显式 `proxy=""` 表示离线/直连构造,完全绕过凭证解析(供测试用);
    不传 `proxy`(默认)才会先看环境变量、缺失时 `load_dotenv()` 一次,
    再缺失就 `RuntimeError`——与 `FotMobClient.__init__` 的策略一致,
    模块 import 本身不读取任何环境变量或 `.env`。
    """

    def __init__(
        self,
        proxy: Any = _DEFAULT_PROXY,
        impersonate: str = "chrome131",
        timeout: int = 25,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        sleep_min: float = 0.3,
        sleep_max: float = 0.8,
    ):
        if proxy is _DEFAULT_PROXY:
            resolved_proxy = os.environ.get("THORDATA_PROXY")
            if not resolved_proxy:
                load_dotenv()
                resolved_proxy = os.environ.get("THORDATA_PROXY")
            if not resolved_proxy:
                raise RuntimeError(
                    "THORDATA_PROXY is required for the default live NowGoalArchiveTransport"
                )
        else:
            resolved_proxy = proxy
        if not isinstance(resolved_proxy, str):
            raise TypeError("proxy must be a string")

        self.proxy = resolved_proxy
        self.proxies = (
            {"http": resolved_proxy, "https": resolved_proxy} if resolved_proxy else {}
        )
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max

    # ── 内部:带重试 + 硬性墙钟超时的请求(镜像 FotMobClient._get) ──────

    def _get(self, url: str, params: dict, *, referer: str | None = None,
            xhr: bool = False) -> str:
        headers = dict(_DEFAULT_HEADERS)
        headers["Referer"] = referer or (BASE_ARCHIVE + "/")
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"

        time.sleep(random.uniform(self.sleep_min, self.sleep_max))

        last_err: NowGoalArchiveError | None = None
        hard_timeout = self.timeout + 10  # thordata 代理隧道偶发挂起,curl_cffi 自身
                                          # timeout 不会触发(FotMobClient 的实证经验)

        for attempt in range(1, self.max_retries + 1):
            result_q: "queue.Queue" = queue.Queue(maxsize=1)

            def _do_request():
                try:
                    resp = cffi_requests.get(
                        url, params=params, headers=headers,
                        proxies=self.proxies, impersonate=self.impersonate,
                        timeout=self.timeout,
                    )
                    result_q.put(("ok", resp))
                except Exception as exc:
                    result_q.put(("err", exc))

            t = threading.Thread(target=_do_request, daemon=True)
            t.start()
            t.join(hard_timeout)

            if t.is_alive():
                log.warning("[NowGoalArchive] hard wall-clock timeout after %ds "
                           "(attempt %d/%d)", hard_timeout, attempt, self.max_retries)
                last_err = NowGoalArchiveTransportError(
                    f"transport error HardTimeout (attempt {attempt}/{self.max_retries})")
            else:
                status, payload = result_q.get()
                if status == "ok":
                    resp = payload
                    if resp.status_code == 200:
                        if looks_blocked(resp.text):
                            # WAF 命中绝不重试(与实时轮询同策略)
                            raise WAFBlockedError(f"NowGoal WAF blocked: {url}")
                        return resp.text
                    log.warning("[NowGoalArchive] HTTP status %s (attempt %d/%d)",
                               resp.status_code, attempt, self.max_retries)
                    last_err = NowGoalArchiveHTTPError(
                        f"HTTP status {resp.status_code} (attempt {attempt}/{self.max_retries})")
                else:
                    exc = payload
                    cls = type(exc).__name__
                    log.warning("[NowGoalArchive] transport error %s (attempt %d/%d)",
                               cls, attempt, self.max_retries)
                    last_err = NowGoalArchiveTransportError(
                        f"transport error {cls} (attempt {attempt}/{self.max_retries})")

            if attempt < self.max_retries:
                sleep = self.retry_delay * (2 ** (attempt - 1)) + random.uniform(0.5, 1.5)
                time.sleep(sleep)

        raise last_err or NowGoalArchiveTransportError("transport error Unknown")

    # ── 端点 ─────────────────────────────────────────────────────────────

    def archive_season(self, ng_league_id: int, season_key: str) -> dict:
        """season-archive JSON。season_key 对自然年联赛是裸年份(如 "2026"),
        对跨年联赛是 "YYYY-YYYY"(如 "2020-2021")——调用方负责传对,
        本函数只做请求 + 自证身份校验,不猜测/不转换格式。"""
        url = f"{BASE_ARCHIVE}/jsData/matchResult/json/{season_key}/s{ng_league_id}_en.json"
        referer = f"{BASE_ARCHIVE}/league/{season_key}/{ng_league_id}"
        text = self._get(url, {}, referer=referer).lstrip("﻿")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise NowGoalArchiveError(
                f"archive_season(ng_league_id={ng_league_id}, season={season_key}) "
                "returned non-JSON response"
            ) from e
        league_info = data.get("LeagueInfo")
        if not (isinstance(league_info, list) and len(league_info) >= 3):
            raise SeasonIdentityError(
                f"archive_season(ng_league_id={ng_league_id}, season={season_key}): "
                "missing LeagueInfo"
            )
        if league_info[0] != ng_league_id or league_info[2] != season_key:
            raise SeasonIdentityError(
                f"archive_season identity mismatch: requested "
                f"(ng_league_id={ng_league_id}, season={season_key}), got "
                f"LeagueInfo[0]={league_info[0]!r}, LeagueInfo[2]={league_info[2]!r}"
            )
        return data

    def mix_history(self, titan_id: str, cid: str = MIX_CID_BET365) -> dict:
        """ah/ou 逐条带时间戳的历史快照。"""
        params = {"type": "14", "id": str(titan_id), "t": "20", "cid": cid,
                  "h": "0", "r1": "0", "r2": "0", "r3": "0"}
        referer = f"{BASE_LIVE}/oddscomp/{titan_id}"
        text = self._get(f"{BASE_LIVE}/ajax/soccerajax", params, referer=referer, xhr=True)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise NowGoalArchiveError(f"mix_history(titan_id={titan_id}) non-JSON") from e
        if data.get("ErrCode") != 0:
            raise NowGoalArchiveError(
                f"mix_history(titan_id={titan_id}) ErrCode={data.get('ErrCode')}")
        return data.get("Data") or {}

    def euro_history(self, titan_id: str, cid: str = EURO_CID_BET365) -> list[dict]:
        """1x2 逐条带时间戳的历史快照。"""
        params = {"sid": str(titan_id), "cid": cid}
        referer = f"{BASE_LIVE}/oddscomp/{titan_id}"
        text = self._get(f"{BASE_LIVE}/football/geteurooddshistorydetail", params,
                         referer=referer, xhr=True)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise NowGoalArchiveError(f"euro_history(titan_id={titan_id}) non-JSON") from e
        return data if isinstance(data, list) else []


# ── 纯解析(无网络,可离线 fixture 测试) ────────────────────────────────


class ArchiveRow:
    """season-archive 里的一场已完赛比赛(解析后的规范形状)。"""

    __slots__ = ("titan_id", "ng_league_id", "kickoff_utc", "home_ng_id",
                 "away_ng_id", "home_score", "away_score")

    def __init__(self, titan_id, ng_league_id, kickoff_utc, home_ng_id, away_ng_id,
                 home_score, away_score):
        self.titan_id = titan_id
        self.ng_league_id = ng_league_id
        self.kickoff_utc = kickoff_utc
        self.home_ng_id = home_ng_id
        self.away_ng_id = away_ng_id
        self.home_score = home_score
        self.away_score = away_score

    def __repr__(self):
        return (f"ArchiveRow(titan_id={self.titan_id!r}, kickoff_utc={self.kickoff_utc!r}, "
                f"score={self.home_score}-{self.away_score})")


_SCORE_RE = re.compile(r"^(\d+)-(\d+)$")


def parse_team_info(payload: dict) -> dict[int, str]:
    """archive_season() 返回值里的 TeamInfo:一次性拿到该联赛全部球队的
    (nowgoal_team_id -> 英文名)。真实结构(2026-08-08 实测):
    `TeamInfo` 是列表,每行 `[ng_team_id, english_name, "", logo_url, 0]`。
    用于给 nowgoal_historical_match_resolution 的候选补队名,让"词典未覆盖
    的球队"能走名称相似度兜底,而不是直接判 no_candidate。
    """
    team_info = payload.get("TeamInfo")
    if not isinstance(team_info, list):
        return {}
    out: dict[int, str] = {}
    for row in team_info:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            tid = int(row[0])
        except (TypeError, ValueError):
            continue
        name = str(row[1] or "").strip()
        if name:
            out[tid] = name
    return out


def parse_archive_season(payload: dict, *, ng_league_id: int) -> list[ArchiveRow]:
    """从 archive_season() 的返回值提取全部已完赛(status==-1)比赛。

    真实行结构(2026-08-08 实测,挪超/瑞典超):
    [titan_id, ng_league_id, status, "YYYY-MM-DD HH:MM"(北京时间),
     home_ng_id, away_ng_id, "H-A"(全场比分), "H-A"(半场比分), ...]
    status == -1 表示已完赛;其余状态(未开赛/进行中等)本函数不处理。
    """
    sched = payload.get("ScheduleList")
    if not isinstance(sched, dict):
        return []
    out: list[ArchiveRow] = []
    for team_key, rounds in sched.items():
        if not isinstance(rounds, dict):
            continue
        for round_key, rows in rounds.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list) or len(row) < 7:
                    continue
                titan_id, lid, status, kickoff_local = row[0], row[1], row[2], row[3]
                if status != -1:
                    continue
                if lid != ng_league_id:
                    continue
                home_ng_id, away_ng_id = row[4], row[5]
                score_m = _SCORE_RE.match(str(row[6] or ""))
                if not score_m:
                    continue
                try:
                    kickoff_utc = archive_kickoff_to_utc(str(kickoff_local))
                except ValueError:
                    continue
                out.append(ArchiveRow(
                    titan_id=str(titan_id), ng_league_id=lid, kickoff_utc=kickoff_utc,
                    home_ng_id=home_ng_id, away_ng_id=away_ng_id,
                    home_score=int(score_m.group(1)), away_score=int(score_m.group(2)),
                ))
    # 去重(archive 按"每个参赛队一份视角"重复列出每场比赛,titan_id 是稳定主键)
    dedup: dict[str, ArchiveRow] = {r.titan_id: r for r in out}
    return sorted(dedup.values(), key=lambda r: r.kickoff_utc)


def two_point_from_mix_history(payload: dict, kickoff_utc: str) -> dict:
    """从 mix_history() 的 Data 提取 ah/ou 两点摘要(严格赛前,见模块 docstring)。

    返回 {"ah": {"opening": {...}, "closing": {...}} | None,
          "ou": {...} | None}。
    某市场赛前零行 -> None,不回退到赛中/赛后行。
    """
    kickoff_dt = datetime.strptime(kickoff_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    kickoff_epoch = kickoff_dt.timestamp()

    out: dict[str, dict | None] = {"ah": None, "ou": None}
    for market in ("ah", "ou"):
        rows = payload.get(market)
        if not isinstance(rows, list):
            continue
        pre = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            mt = r.get("mt")
            odds = r.get("odds")
            if mt is None or not isinstance(odds, dict):
                continue
            try:
                mt_val = float(mt)
            except (TypeError, ValueError):
                continue
            if mt_val >= kickoff_epoch:
                continue  # 严格赛前;赛中/赛后行一律丢弃,不回退
            try:
                u, g, d = float(odds["u"]), float(odds["g"]), float(odds["d"])
            except (KeyError, TypeError, ValueError):
                continue
            if u <= 0 or d <= 0:
                continue  # 该行是占位/收线标记(u=g=d=0 之类),不是真实报价
            pre.append((mt_val, u, g, d))
        if not pre:
            continue
        pre.sort(key=lambda t: t[0])
        _, u0, g0, d0 = pre[0]
        _, u1, g1, d1 = pre[-1]
        if market == "ah":
            out["ah"] = {
                "opening": {"home": u0, "line": g0, "away": d0},
                "closing": {"home": u1, "line": g1, "away": d1},
            }
        else:
            out["ou"] = {
                "opening": {"over": u0, "line": g0, "under": d0},
                "closing": {"over": u1, "line": g1, "under": d1},
            }
    return out


_EURO_TIME_FMT = "%Y,%m,%d,%H,%M,%S"


def two_point_from_euro_history(rows: list, kickoff_utc: str) -> dict | None:
    """从 euro_history() 的返回值提取 1x2 两点摘要(严格赛前)。"""
    kickoff_dt = datetime.strptime(kickoff_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    pre = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ts = r.get("TimeShow")
        if not ts:
            continue
        try:
            dt = datetime.strptime(ts, _EURO_TIME_FMT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt >= kickoff_dt:
            continue  # 严格赛前
        try:
            home = float(r["HomeWin"])
            draw = float(r["Standoff"])
            away = float(r["GuestWin"])
        except (KeyError, TypeError, ValueError):
            continue
        if home <= 0 or draw <= 0 or away <= 0:
            continue
        pre.append((dt, home, draw, away))
    if not pre:
        return None
    pre.sort(key=lambda t: t[0])
    _, h0, d0, a0 = pre[0]
    _, h1, d1, a1 = pre[-1]
    return {
        "opening": {"home": h0, "draw": d0, "away": a0},
        "closing": {"home": h1, "draw": d1, "away": a1},
    }
