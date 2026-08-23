"""
fotmob_client.py — 无浏览器 FotMob 数据抓取客户端
使用 curl_cffi（Chrome TLS 指纹模拟）+ ThorData 住宅代理轮换
绕过 Cloudflare Turnstile，从 SSR 页面提取 __NEXT_DATA__

用法:
    from fotmob_client import FotMobClient
    client = FotMobClient()
    match = client.match_details(4193490)
    matches = client.daily_matches("20260411")

    # DB-ready 解析方法（字段名与数据库列名一致）：
    pp = client.match_details(4193490)
    dim       = client.parse_match_dim(pp, 4193490, league_id=47, date="2026-04-11")
    shots     = client.parse_shotmap_records(pp, 4193490)
    t_stats   = client.parse_team_stats_records(pp, 4193490)
    p_records = client.parse_player_stats_records(pp, 4193490)
"""

import os
import json
import queue
import re
import threading
import time
import random
import logging
from typing import Optional, Union, List, Dict, Any

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ThorData 住宅代理（轮换会话，每次请求自动换 IP）
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_PROXY = object()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fotmob.com/",
}

# __NEXT_DATA__ 正则（编译一次复用）
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


# ─────────────────────────────────────────────────────────────────────────────
# 内部：球员 stat 解析工具（支持新旧两种 FotMob 格式）
# ─────────────────────────────────────────────────────────────────────────────

def _build_stat_lookup(stats_sections: list) -> dict:
    """
    将 FotMob 新版「分区段列表」格式转换为扁平 {key: value} 字典。
    输入: [{"title": "Top stats", "stats": {"Goals": {"key": "goals", "stat": {"value": 1}}, ...}}, ...]
    输出: {"goals": 1, "minutes_played": 89, "rating_title": 6.59, ...}
    """
    lookup = {}
    if not isinstance(stats_sections, list):
        return lookup
    for section in stats_sections:
        section_stats = section.get("stats", {})
        if isinstance(section_stats, dict):
            for _, stat_obj in section_stats.items():
                if not isinstance(stat_obj, dict):
                    continue
                key = stat_obj.get("key")
                stat_inner = stat_obj.get("stat", {})
                if isinstance(stat_inner, dict):
                    val = stat_inner.get("value")
                    if key and val is not None:
                        lookup[key] = val
        elif isinstance(section_stats, list):
            for item in section_stats:
                key = item.get("key")
                stat_inner = item.get("stat", {})
                if isinstance(stat_inner, dict):
                    val = stat_inner.get("value")
                    if key and val is not None:
                        lookup[key] = val
    return lookup


def _iso_to_ymd(value: Any) -> Optional[str]:
    """
    从 ISO-8601 字符串（如 "2026-05-19T18:30:00.000Z"）提取 "YYYY-MM-DD"。
    通过下标 4/7 是否为 '-' 判断是否真的是 ISO 格式，
    避免把人类可读字符串（如 "Tue, May 19, 2026, 18:30 UTC"）误判为 ISO 后截断出垃圾。
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    if value[4] == "-" and value[7] == "-":
        return value[:10]
    return None


def _extract_iso_date(general: dict, content: dict) -> Optional[str]:
    """
    提取比赛日期，按优先级尝试多个来源：
      1. general.matchTimeUTCDate（新版 API 的标准 ISO 字段）
      2. content.matchFacts.infoBox["Match Date"].utcTime（备选 ISO 字段）
      3. general.matchTimeUTC（旧版可能是 ISO，也可能是人类可读串，仅在确认是 ISO 时采用）
      4. 兜底：直接截取 matchTimeUTC 前 10 个字符（可能不准确，仅作最后手段）
    """
    d = _iso_to_ymd(general.get("matchTimeUTCDate"))
    if d:
        return d

    info_box = (content.get("matchFacts", {}) or {}).get("infoBox", {}) or {}
    match_date = info_box.get("Match Date")
    if isinstance(match_date, dict):
        d = _iso_to_ymd(match_date.get("utcTime"))
        if d:
            return d

    legacy = general.get("matchTimeUTC", "")
    d = _iso_to_ymd(legacy)
    if d:
        return d

    return legacy[:10] if legacy else None


def _extract_kickoff_utc(general: dict, content: dict) -> Optional[str]:
    """
    提取精确 UTC 开球时刻(带时间部分的完整 ISO),来源优先级同 _extract_iso_date。
    来源只给日期/不可解析 → None,不得补当天 00:00 伪装精确时间(CLAUDE.md §6.2.1)。
    """
    try:
        from backend.db.util import normalize_utc_iso  # 包内运行(worker/providers)
    except ImportError:
        from db.util import normalize_utc_iso          # 脚本式运行(cwd=backend)

    candidates = [general.get("matchTimeUTCDate")]
    info_box = (content.get("matchFacts", {}) or {}).get("infoBox", {}) or {}
    match_date = info_box.get("Match Date")
    if isinstance(match_date, dict):
        candidates.append(match_date.get("utcTime"))
    candidates.append(general.get("matchTimeUTC"))
    for value in candidates:
        normalized = normalize_utc_iso(value)
        if normalized:
            return normalized
    return None


# dim_match.status 的封闭词表(CLAUDE.md §6.2.1 / §6.3):四个值,没有第五个。
# 全站消费方只认这四个:poll_windows.py 的 upcoming_precise_matches()
# (status='NotStarted')、queries/matches.py(status IN ('NotStarted','InPlay'))、
# 多处已完赛判定(status='Finish')。把来源的 reason.short 原样透传(旧代码的
# 行为)会让该场比赛对上述每一个查询同时不可见,和历史 bug 里恒返回 'Unknown'
# 是同一类静默失踪缺陷,因此这里改成封闭四值而不是透传来源原始短码。
STATUS_FINISH = "Finish"
STATUS_NOT_STARTED = "NotStarted"
STATUS_IN_PLAY = "InPlay"
STATUS_CANCELLED = "Cancelled"


def derive_match_status(status_obj: Any) -> str:
    """FotMob header.status 的布尔三元组 → dim_match.status 封闭词表。

    parse_match_dim(match_details 详情页)与
    backend/ingest/ingest_future_fixtures.py 的 _status_from_fixture(赛程列表页)
    读的是同一个 status 对象形状(started/finished/cancelled),必须共用本函数,
    不能各写一份、慢慢互相漂移。

    ⚠️ 真实赛前 header.status 没有 `reason` 键(只有
    cancelled/finished/halfs/started/timezone/utcTime 等)。旧代码
    `status_obj.get("reason", {}).get("short", "Unknown")` 因此对任何赛前
    比赛恒返回 'Unknown',导致该场比赛被 poll_windows.upcoming_precise_matches
    (过滤 status='NotStarted')永久排除出赔率轮询窗口。
    """
    st = status_obj if isinstance(status_obj, dict) else {}
    if st.get("finished"):
        return STATUS_FINISH          # 含 FT / AET / Pen / AW(点球告负判负胜)等来源短码
    if st.get("cancelled"):
        return STATUS_CANCELLED
    if st.get("started"):
        return STATUS_IN_PLAY
    return STATUS_NOT_STARTED


def _split_scores_str(value: Any) -> "tuple[Optional[int], Optional[int]]":
    """把 "71-27" 形式的比分字符串拆成 (进球, 失球)，解析失败返回 (None, None)。"""
    if not isinstance(value, str) or "-" not in value:
        return None, None
    try:
        a, b = value.split("-", 1)
        return int(a.strip()), int(b.strip())
    except Exception:
        return None, None


def _get_stat_value(stats_data: Any, *keys: str) -> Optional[Any]:
    """
    从多种 FotMob stat 格式中提取数值。
    支持:
      - dict 格式: {"goals": {"stat": {"value": 1}}}
      - list 格式: [{"key": "goals", "stat": {"value": 1}}]
      - 直接数值: {"goals": 1}
    多个 key 按顺序尝试（别名兼容）。
    """
    for key in keys:
        if isinstance(stats_data, dict):
            s = stats_data.get(key)
            if s is None:
                continue
            if isinstance(s, (int, float)):
                return s
            if isinstance(s, dict):
                inner = s.get("stat") or {}
                v = inner.get("value")
                if v is not None:
                    return v
                v = s.get("num") or s.get("value")
                if v is not None:
                    return v
        elif isinstance(stats_data, list):
            for item in stats_data:
                if item.get("key") == key:
                    stat = item.get("stat", {})
                    v = stat.get("value")
                    if v is not None:
                        return v
    return None


class FotMobError(RuntimeError):
    """FotMob request failure whose message is safe for logs and callers."""


class FotMobTransportError(FotMobError):
    """Transport failure with all external exception text removed."""


class FotMobHTTPError(FotMobError):
    """Non-success HTTP response with response body deliberately omitted."""


class FotMobDecodeError(FotMobError):
    """Response decoding failure with external content deliberately omitted."""


def _safe_exception_class_name(exc: BaseException) -> str:
    """Return only a Python identifier, never external exception text."""
    name = type(exc).__name__
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "Exception"


_SAFE_RESPONSE_OPERATIONS = frozenset({
    "check_ip",
    "daily_matches",
    "fetch_stat_leaderboard",
    "league_matches",
    "match_details",
    "team_data",
})


def _safe_response_operation_name(operation: str) -> str:
    """Return a fixed internal operation label, never caller/external text."""
    return operation if operation in _SAFE_RESPONSE_OPERATIONS else "response"


class FotMobClient:
    """无浏览器 FotMob 数据客户端。

    模块 import 不加载 ``.env``、不读取代理环境变量。显式 ``proxy=""`` 表示
    离线/直连构造且完全绕过凭证解析；只有未传 ``proxy`` 的默认 live client
    才先看已有环境变量，并在缺失时加载一次 ``.env``。
    """

    def __init__(
        self,
        proxy: Any = _DEFAULT_PROXY,
        impersonate: str = "chrome131",
        timeout: int = 25,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        if proxy is _DEFAULT_PROXY:
            resolved_proxy = os.environ.get("THORDATA_PROXY")
            if not resolved_proxy:
                load_dotenv()
                resolved_proxy = os.environ.get("THORDATA_PROXY")
            if not resolved_proxy:
                raise RuntimeError(
                    "THORDATA_PROXY is required for the default live FotMobClient"
                )
        else:
            resolved_proxy = proxy
        if not isinstance(resolved_proxy, str):
            raise TypeError("proxy must be a string")

        self.proxy = resolved_proxy
        self.proxies = (
            {"http": resolved_proxy, "https": resolved_proxy}
            if resolved_proxy else {}
        )
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    # ─────────────────────────────────────────────────────────────────────
    # 内部：带重试的请求
    # ─────────────────────────────────────────────────────────────────────
    def _get(self, url: str, headers: Optional[dict] = None) -> cffi_requests.Response:
        hdrs = {**DEFAULT_HEADERS, **(headers or {})}
        last_err: Optional[FotMobError] = None
        # 硬性墙钟超时：thordata 代理隧道偶发"连接建立但代理无响应"的挂起，
        # curl_cffi 自身的 timeout 参数在这种场景下不会触发（实测挂起过 15+ 分钟）。
        # 用 daemon 线程 + 墙钟超时兜底，到点强制放弃本次尝试、进入下一次重试。
        hard_timeout = self.timeout + 10

        for attempt in range(1, self.max_retries + 1):
            result_q: "queue.Queue" = queue.Queue(maxsize=1)

            def _do_request():
                try:
                    resp = cffi_requests.get(
                        url,
                        headers=hdrs,
                        proxies=self.proxies,
                        impersonate=self.impersonate,
                        timeout=self.timeout,
                    )
                    raw_status = getattr(resp, "status_code", None)
                    status_code = (
                        raw_status
                        if type(raw_status) is int
                        else None
                    )
                    result_q.put(("ok", (resp, status_code)))
                except Exception as exc:
                    result_q.put(("err", exc))

            t = threading.Thread(target=_do_request, daemon=True)
            t.start()
            t.join(hard_timeout)

            if t.is_alive():
                # 线程仍未返回：硬性墙钟超时命中，放弃本次尝试（线程作为 daemon 泄漏，
                # 不阻塞进程退出，底层连接最终会自行断开或随进程退出回收）
                log.warning(
                    "[FotMob] hard wall-clock timeout after %ds "
                    "(attempt %d/%d)",
                    hard_timeout, attempt, self.max_retries,
                )
                last_err = FotMobTransportError(
                    "FotMob transport error HardTimeout "
                    f"(attempt {attempt}/{self.max_retries})"
                )
            else:
                status, payload = result_q.get()
                if status == "ok":
                    r, status_code = payload
                    if status_code == 200:
                        return r
                    if status_code is None:
                        log.warning(
                            "[FotMob] HTTP status unavailable "
                            "(attempt %d/%d)",
                            attempt, self.max_retries,
                        )
                        last_err = FotMobHTTPError(
                            "FotMob HTTP error: status unavailable "
                            f"(attempt {attempt}/{self.max_retries})"
                        )
                    else:
                        log.warning(
                            "[FotMob] HTTP status %d (attempt %d/%d)",
                            status_code, attempt, self.max_retries,
                        )
                        last_err = FotMobHTTPError(
                            f"FotMob HTTP error: HTTP status {status_code} "
                            f"(attempt {attempt}/{self.max_retries})"
                        )
                else:
                    e = payload
                    exception_class = _safe_exception_class_name(e)
                    log.warning(
                        "[FotMob] transport error %s (attempt %d/%d)",
                        exception_class, attempt, self.max_retries,
                    )
                    last_err = FotMobTransportError(
                        f"FotMob transport error {exception_class} "
                        f"(attempt {attempt}/{self.max_retries})"
                    )

            if attempt < self.max_retries:
                # 指数退避：第 N 次重试基础延迟翻倍，代理池持续抖动时不再用同一节奏
                # 反复空砸；轮换会话下换一次连接大概率就是换一个出口 IP，多等一拍
                # 更可能等到健康节点。
                sleep = self.retry_delay * (2 ** (attempt - 1)) + random.uniform(0.5, 1.5)
                time.sleep(sleep)

        if last_err is None:
            last_err = FotMobTransportError(
                "FotMob transport error Unknown "
                f"(attempts {self.max_retries})"
            )
        raise last_err from None

    # ─────────────────────────────────────────────────────────────────────
    # 内部：从 SSR HTML 提取 __NEXT_DATA__
    # ─────────────────────────────────────────────────────────────────────
    def _raise_response_decode_error(
        self,
        operation: str,
        exception_class: str,
    ) -> None:
        safe_operation = _safe_response_operation_name(operation)
        safe_exception_class = (
            exception_class
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", exception_class)
            else "Exception"
        )
        log.warning(
            "[FotMob] response decode error operation=%s type=%s",
            safe_operation,
            safe_exception_class,
        )
        raise FotMobDecodeError(
            "FotMob response decode error "
            f"{safe_operation} {safe_exception_class}"
        ) from None

    def _decode_json_response(
        self,
        response: cffi_requests.Response,
        operation: str,
    ) -> dict:
        exception_class = None
        try:
            return response.json()
        except Exception as exc:
            exception_class = _safe_exception_class_name(exc)

        self._raise_response_decode_error(operation, exception_class)

    def _read_response_text(
        self,
        response: cffi_requests.Response,
        operation: str,
    ) -> str:
        exception_class = None
        try:
            return response.text
        except Exception as exc:
            exception_class = _safe_exception_class_name(exc)

        self._raise_response_decode_error(operation, exception_class)

    def _extract_next_data(
        self,
        html: str,
        operation: str = "match_details",
    ) -> dict:
        m = _NEXT_DATA_RE.search(html)
        if not m:
            self._raise_response_decode_error(
                operation, "MissingNextData",
            )

        exception_class = None
        try:
            return json.loads(m.group(1))
        except Exception as exc:
            exception_class = _safe_exception_class_name(exc)

        self._raise_response_decode_error(operation, exception_class)

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API — 原始数据
    # ─────────────────────────────────────────────────────────────────────

    def check_ip(self) -> dict:
        """验证代理 IP 是否正常工作，返回 IP 信息"""
        # 复用同一安全 transport 边界，避免代理连通性检查把底层异常原文直接
        # 抛给调用方。该方法仍只会在显式调用时访问外部服务。
        r = self._get("https://httpbin.org/ip")
        return self._decode_json_response(r, "check_ip")

    def daily_matches(self, date: str) -> dict:
        """
        获取某天的比赛列表
        date 格式: "20260411"
        返回: {"leagues": [...], "date": "..."}
        """
        url = f"https://www.fotmob.com/api/data/matches?date={date}&timezone=Asia%2FShanghai&ccode3=JPN"
        r = self._get(url, headers={"Accept": "application/json"})
        return self._decode_json_response(r, "daily_matches")

    def match_details(self, match_id: Union[int, str]) -> dict:
        """
        获取比赛详情（通过 SSR 页面绕过 Turnstile）
        返回 pageProps: {general, header, content, ...}
        content 包含: lineup, shotmap, stats, playerStats, h2h, momentum 等

        注意：SSR 页面有 CDN 缓存（5~20 分钟），赛前首发确认阶段会有轻微滞后。
        """
        url = f"https://www.fotmob.com/match/{match_id}"
        r = self._get(url, headers={"Accept": "text/html"})
        html = self._read_response_text(r, "match_details")
        nd = self._extract_next_data(html, "match_details")
        props = nd.get("props") if isinstance(nd, dict) else None
        page_props = (
            props.get("pageProps")
            if isinstance(props, dict)
            else None
        )
        if (
            not isinstance(page_props, dict)
            or not isinstance(page_props.get("general"), dict)
            or not page_props["general"]
        ):
            self._raise_response_decode_error(
                "match_details", "InvalidPageProps",
            )
        return page_props

    def match_lineup(self, match_id: Union[int, str]) -> Optional[dict]:
        """获取阵容，返回 {homeTeam, awayTeam, ...} 或 None"""
        pp = self.match_details(match_id)
        return pp.get("content", {}).get("lineup")

    def match_shotmap(self, match_id: Union[int, str]) -> list:
        """获取射门图原始数据，返回 shots 列表（原始字段名）"""
        pp = self.match_details(match_id)
        sm = pp.get("content", {}).get("shotmap", {})
        return sm.get("shots", [])

    def match_stats(self, match_id: Union[int, str]) -> dict:
        """获取比赛统计（All/FirstHalf/SecondHalf）"""
        pp = self.match_details(match_id)
        return pp.get("content", {}).get("stats", {})

    def match_player_stats(self, match_id: Union[int, str]) -> dict:
        """获取球员详细统计，返回 {player_id: {...}, ...}"""
        pp = self.match_details(match_id)
        return pp.get("content", {}).get("playerStats", {})

    def league_matches(self, league_id: Union[int, str], season: str = "") -> dict:
        """
        获取联赛比赛列表
        league_id: FotMob 联赛 ID（如 47=英超, 87=西甲）
        """
        url = f"https://www.fotmob.com/api/data/leagues?id={league_id}"
        if season:
            url += f"&season={season}"
        r = self._get(url, headers={"Accept": "application/json"})
        return self._decode_json_response(r, "league_matches")

    def team_data(self, team_id: Union[int, str], season: str = "") -> dict:
        """
        获取球队总览数据（含 details/fixtures/overview/table/squad/history 等）。

        新增于球队赛程可行性 pilot（该研究目录已于 2026-08-14 清理删除，
        结论见 docs/audits/team-schedule-pilot.md）。

        ⚠️ 实测（2026-07-23，team_id=8456）：`season` 参数对返回内容**没有
        可观测影响**——该端点不是"按赛季查询历史赛程"接口，而是以当前时刻
        为中心的"最近 + 未来"滚动窗口（实测 50 场，`details.latestSeason`/
        `overview.season` 恒为来源当前赛季，不随 season 参数变化）。调用方
        不得假设传入 season 就能取回该赛季完整历史赛程。
        """
        url = f"https://www.fotmob.com/api/data/teams?id={team_id}"
        if season:
            url += f"&season={season}"
        r = self._get(url, headers={"Accept": "application/json"})
        return self._decode_json_response(r, "team_data")

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API — DB-ready 解析方法（字段名与数据库列名一致）
    # ─────────────────────────────────────────────────────────────────────

    def parse_match_dim(
        self,
        page_props: dict,
        match_id: Union[int, str],
        league_id: Optional[int] = None,
        date: Optional[str] = None,
        season: str = "2025/2026",
    ) -> dict:
        """
        从 pageProps 中提取 dim_match 表所需的所有字段。

        返回字段（对应 dim_match 列）:
          Match_ID, Season, League_ID, Date,
          kickoff_at_utc, kickoff_precision, kickoff_source,
          Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,
          home_score, away_score, status,
          Temperature, Wind_Speed, Referee, Match_Round, Who_Lost_On_Penalties,
          Venue_Name, Venue_City, Venue_Country, Weather_Description
        """
        general = page_props.get("general", {})
        header  = page_props.get("header", {})
        content = page_props.get("content", {})

        # ── 球队信息 ────────────────────────────────────────────────────
        # general.homeTeam / general.awayTeam 是最直接的来源
        home_team = general.get("homeTeam") or {}
        away_team = general.get("awayTeam") or {}

        # header.teams 作为备选（[home, away]）
        header_teams = header.get("teams", [{}, {}])
        if not home_team and len(header_teams) > 0:
            home_team = header_teams[0]
        if not away_team and len(header_teams) > 1:
            away_team = header_teams[1]

        home_id   = home_team.get("id")
        away_id   = away_team.get("id")
        home_name = home_team.get("name")
        away_name = away_team.get("name")

        # ── 比分 ────────────────────────────────────────────────────────
        # 注意：解析失败时不能兜底成 0-0——那和"真实 0-0 平局"在库里完全无法区分。
        # 老赛季 scoreStr 格式若有出入、且 team 对象里也真没有 score 字段，
        # 应该让 home_score/away_score 落成 NULL（"未知"），而不是伪造出一个比分。
        status_obj = header.get("status", {})
        score_str  = status_obj.get("scoreStr", "")
        try:
            parts    = score_str.split(" - ")
            h_score  = int(parts[0].strip())
            a_score  = int(parts[1].strip())
        except Exception:
            raw_h, raw_a = home_team.get("score"), away_team.get("score")
            try:
                h_score = int(raw_h) if raw_h is not None else None
                a_score = int(raw_a) if raw_a is not None else None
            except (TypeError, ValueError):
                h_score = a_score = None

        # ── 比赛状态 ─────────────────────────────────────────────────────
        match_status = derive_match_status(status_obj)

        # ── 联赛 ID / 日期（来自调用方，或从 general 推断）──────────────
        if league_id is None:
            league_id = general.get("leagueId") or general.get("parentLeagueId")
        if date is None:
            date = _extract_iso_date(general, content)

        # ── 裁判 ────────────────────────────────────────────────────────
        referee = None
        ref_data = general.get("referee")
        if isinstance(ref_data, dict):
            referee = ref_data.get("name") or ref_data.get("text")
        elif isinstance(ref_data, str):
            referee = ref_data
        # 备选：content.referee
        if not referee:
            ref_content = content.get("referee")
            if isinstance(ref_content, dict):
                referee = ref_content.get("name") or ref_content.get("text")
            elif isinstance(ref_content, str):
                referee = ref_content
        # 备选：content.matchFacts.infoBox.Referee（新版 API 位置）
        if not referee:
            info_box = (content.get("matchFacts", {}) or {}).get("infoBox", {}) or {}
            ref_ib = info_box.get("Referee")
            if isinstance(ref_ib, dict):
                referee = ref_ib.get("text") or ref_ib.get("name")
            elif isinstance(ref_ib, str):
                referee = ref_ib

        # ── 轮次 ────────────────────────────────────────────────────────
        match_round = (
            general.get("matchRound")
            or general.get("roundId")
            or general.get("round")
        )
        if match_round is None:
            match_round = content.get("matchFacts", {}).get("matchRound")

        # ── 天气 ────────────────────────────────────────────────────────
        temperature = None
        wind_speed  = None
        weather_description = None
        weather_raw = content.get("weather") or general.get("weather")
        if isinstance(weather_raw, dict):
            temperature = weather_raw.get("temperature") or weather_raw.get("temp")
            wind_speed  = weather_raw.get("windSpeed") or weather_raw.get("wind")
            # description 优先于 defaultTitle："Partly Cloudy/Wind" 比 "Windy" 信息量大；
            # 两者都取自英文原始来源，不在这里翻中文（翻译是展示层的事，见 §6.2 —— 库里
            # 只存来源原文，不存加工结果，否则来源文案改了这里也不会同步）。
            weather_description = weather_raw.get("description") or weather_raw.get("defaultTitle")
            # FotMob 有时把天气包在 .conditions 里
            cond = weather_raw.get("conditions", {})
            if isinstance(cond, dict):
                temperature = temperature or cond.get("temperature") or cond.get("temp")
                wind_speed  = wind_speed  or cond.get("windSpeed")
                weather_description = weather_description or cond.get("description")

        # ── 场馆 ────────────────────────────────────────────────────────
        # 只有一个真实来源位置(content.matchFacts.infoBox.Stadium)，不像裁判/
        # 天气那样存在多个历史备选位置——没有就是没有，不额外猜测其它路径。
        venue_name = venue_city = venue_country = None
        info_box_for_venue = (content.get("matchFacts", {}) or {}).get("infoBox", {}) or {}
        stadium = info_box_for_venue.get("Stadium")
        if isinstance(stadium, dict):
            venue_name    = stadium.get("name")
            venue_city    = stadium.get("city")
            venue_country = stadium.get("country")

        # ── 点球大战失利方 ───────────────────────────────────────────────
        who_lost_penalties = None
        # general.penaltiesInfo 或 general.penaltyScore
        penalties_info = general.get("penaltiesInfo") or general.get("penaltyScore")
        if isinstance(penalties_info, dict):
            loser = penalties_info.get("loser") or penalties_info.get("whoLostOnPenalties")
            who_lost_penalties = str(loser) if loser is not None else None
        elif general.get("whoLostOnPenalties"):
            who_lost_penalties = str(general["whoLostOnPenalties"])

        kickoff_at_utc = _extract_kickoff_utc(general, content)

        return {
            "Match_ID":             int(match_id),
            "Season":               season,
            "League_ID":            league_id,
            "Date":                 date,
            "kickoff_at_utc":       kickoff_at_utc,
            # provenance(§6.2.1):有精确时刻 → exact + 'fotmob:match_details';
            # 只有日期 → date_only;连日期都没有 → unknown。不编造来源。
            "kickoff_precision":    ("exact" if kickoff_at_utc else ("date_only" if date else "unknown")),
            "kickoff_source":       ("fotmob:match_details" if kickoff_at_utc else None),
            "Home_Team_ID":         home_id,
            "Away_Team_ID":         away_id,
            "Home_Team_Name":       home_name,
            "Away_Team_Name":       away_name,
            "home_score":           h_score,
            "away_score":           a_score,
            "status":               match_status,
            "Referee":              referee,
            "Match_Round":          str(match_round) if match_round is not None else None,
            "Temperature":          str(temperature) if temperature is not None else None,
            "Wind_Speed":           str(wind_speed)  if wind_speed  is not None else None,
            "Who_Lost_On_Penalties": who_lost_penalties,
            "Venue_Name":           venue_name,
            "Venue_City":           venue_city,
            "Venue_Country":        venue_country,
            "Weather_Description":  weather_description,
        }

    def parse_shotmap_records(
        self,
        page_props: dict,
        match_id: Union[int, str],
    ) -> List[dict]:
        """
        从 pageProps 中提取 fact_shotmap 表所需的所有字段。

        返回字段列表（对应 fact_shotmap 列）:
          Match_ID, Player_ID, Team_ID, Minute, Period,
          X_Coord, Y_Coord, xG, xGOT,
          Situation, Outcome, Shot_Type,
          Shot_ID, Is_Blocked, Is_On_Target, Is_From_Inside_Box,
          Minute_Added, Keeper_ID

        说明:
          - Situation: 射门情境（RegularPlay/FromCorner/SetPiece/FreeKick/Penalty）
            FotMob SSR 数据中该字段有时缺失（旧场次），需后续补采
          - xGOT:      expectedGoalsOnTarget，SSR 数据中有时缺失
          - Shot_Type: 射门方式（LeftFoot/RightFoot/Header/OtherBodyParts，
            此前这行注释误写成 Head/OtherBodyPart，与真实枚举值不符）
          - Period:    比赛阶段（FirstHalf/SecondHalf/ExtraTimeFirstHalf 等）
          - Outcome:   真实枚举只有 4 个值 Goal/AttemptSaved/Miss/Post
            （库内实测 33 万行验证过，从无 BlockedShot——FotMob 把"被后卫
            封堵"也计入 AttemptSaved，用下面新增的 Is_Blocked 才能拆开）

        2026-08-23 补齐(对照 FotMob 官方安卓包核实,原始 payload 本来就带
        这些字段,只是此前没解析):
          - Shot_ID:            原始 id,可作稳定行键(此前 ingest_match.py
            误称"没有稳定的行 id"，据此用整场 DELETE+INSERT 而非按行更新)
          - Is_Blocked:         被封堵(与门将扑出互斥，用于拆分 AttemptSaved)
          - Is_On_Target:       是否射正（精确值，不再靠 Outcome 猜）
          - Is_From_Inside_Box: 是否禁区内射门（校验信号，不替代
            backend/queries/matchup.py 的坐标法——见该文件模块 docstring）
          - Minute_Added:       补时分钟（此前缺失导致所有 90+X 分钟射门
            坍缩到第 90 分钟）
          - Keeper_ID:          面对的门将 player id
        """
        content = page_props.get("content", {})
        shots_raw = content.get("shotmap", {}).get("shots", [])

        records = []
        for s in shots_raw:
            records.append({
                "Match_ID":  int(match_id),
                "Player_ID": s.get("playerId"),
                "Team_ID":   s.get("teamId"),
                "Minute":    s.get("min"),
                "Period":    s.get("period"),
                "X_Coord":   s.get("x"),
                "Y_Coord":   s.get("y"),
                "xG":        s.get("expectedGoals"),
                "xGOT":      s.get("expectedGoalsOnTarget"),
                "Situation": s.get("situation"),   # 可能为 None（SSR 旧场次缺失）
                "Outcome":   s.get("eventType"),   # Goal/AttemptSaved/Miss/Post
                "Shot_Type": s.get("shotType"),    # LeftFoot/RightFoot/Header/OtherBodyParts
                "Shot_ID":             s.get("id"),
                "Is_Blocked":          s.get("isBlocked"),
                "Is_On_Target":        s.get("isOnTarget"),
                "Is_From_Inside_Box":  s.get("isFromInsideBox"),
                "Minute_Added":        s.get("minAdded"),
                "Keeper_ID":           s.get("keeperId"),
            })
        return records

    def parse_team_stats_records(
        self,
        page_props: dict,
        match_id: Union[int, str],
    ) -> List[dict]:
        """
        从 pageProps 中提取 fact_team_match_stats 表记录（主队+客队，每个 Period 各一行）。

        返回字段（对应 fact_team_match_stats 列）:
          Match_ID, Team_ID, Period, Goals, BallPossesion,
          expected_goals, total_shots, ShotsOnTarget, ... 等 40+ 个统计项。
        """
        general = page_props.get("general", {})
        header  = page_props.get("header", {})
        content = page_props.get("content", {})
        stats   = content.get("stats") or {}

        # ── 球队 ID / 比分 ─────────────────────────────────────────────
        home_team = general.get("homeTeam") or {}
        away_team = general.get("awayTeam") or {}
        header_teams = header.get("teams", [{}, {}])
        if not home_team and len(header_teams) > 0:
            home_team = header_teams[0]
        if not away_team and len(header_teams) > 1:
            away_team = header_teams[1]

        hid = home_team.get("id")
        aid = away_team.get("id")

        # 同 parse_match_dim：解析失败且 team 对象也无 score 字段时留 None，
        # 不伪造 0-0（老赛季 scoreStr 格式有出入时尤其要避免这种静默错填）。
        status_obj = header.get("status", {})
        score_str  = status_obj.get("scoreStr", "")
        try:
            parts   = score_str.split(" - ")
            h_score = int(parts[0].strip())
            a_score = int(parts[1].strip())
        except Exception:
            raw_h, raw_a = home_team.get("score"), away_team.get("score")
            try:
                h_score = int(raw_h) if raw_h is not None else None
                a_score = int(raw_a) if raw_a is not None else None
            except (TypeError, ValueError):
                h_score = a_score = None

        # ── 分段统计 ─────────────────────────────────────────────────────
        # stats.Periods: {"All": {...}, "FirstHalf": {...}, "SecondHalf": {...}}
        periods_data = stats.get("Periods", {})
        if not periods_data and "stats" in stats:
            periods_data = {"All": stats}

        records = []
        for period_name, p_data in periods_data.items():
            hs: Dict[str, Any] = {
                "Match_ID": int(match_id),
                "Team_ID":  hid,
                "Period":   period_name,
                "Goals":    h_score if period_name == "All" else home_team.get("score"),
            }
            as_: Dict[str, Any] = {
                "Match_ID": int(match_id),
                "Team_ID":  aid,
                "Period":   period_name,
                "Goals":    a_score if period_name == "All" else away_team.get("score"),
            }

            for cat in p_data.get("stats", []):
                for item in cat.get("stats", []):
                    k = (item.get("key") or item.get("title", "")).replace(" ", "_")
                    v = item.get("stats", [])
                    if len(v) == 2:
                        try:
                            hs[k] = float(str(v[0]).split("%")[0].split("(")[0].strip())
                            as_[k] = float(str(v[1]).split("%")[0].split("(")[0].strip())
                        except Exception:
                            hs[k], as_[k] = v[0], v[1]

            records.extend([hs, as_])

        return records

    def parse_player_stats_records(
        self,
        page_props: dict,
        match_id: Union[int, str],
    ) -> List[dict]:
        """
        从 pageProps 中提取 fact_player_match_stats 表记录（每名球员一行）。
        同时返回 dim_player 所需的 {player_id: name} 映射。

        支持新旧两种 FotMob playerStats 格式：
          - 新格式: {player_id: {name, stats: [sections], ...}}
          - 旧格式: {players: [{id, stats, ...}]}
          - 降级到 content.lineup

        返回: list[dict]，每个 dict 字段对应 fact_player_match_stats 列。
        附带额外 key "player_name" 用于同步 dim_player。
        """
        content = page_props.get("content", {})
        ps_data = content.get("playerStats") or {}   # 部分比赛返回 null，需 or {} 兜底
        is_lineup_fallback = False

        # ── 格式检测 ─────────────────────────────────────────────────────
        is_new_format = (
            isinstance(ps_data, dict)
            and bool(ps_data)
            and all(
                isinstance(v, dict) and isinstance(v.get("stats"), list)
                for v in list(ps_data.values())[:3]
            )
        )

        if is_new_format:
            players_raw = list(ps_data.values())
            log.info("[PlayerStats] 新格式（player_id keyed dict），共 %d 名球员", len(players_raw))
        else:
            players_raw = ps_data.get("players", [])

        if not players_raw:
            players_raw = content.get("players", [])

        if not players_raw:
            # 降级到 lineup
            is_lineup_fallback = True
            lineup_obj = content.get("lineup", {})
            lineup_list = lineup_obj.get("lineup") or lineup_obj.get("confirmedLineup") or []
            for team_lineup in lineup_list:
                team_id = team_lineup.get("teamId") or team_lineup.get("id")
                for group in ["players", "bench"]:
                    is_starter = (group == "players")
                    for p in team_lineup.get(group, []):
                        p_copy = dict(p)
                        p_copy["teamId"]      = team_id
                        p_copy["_is_starter"] = is_starter
                        players_raw.append(p_copy)

        # ── 从 lineup 构建 player_id → name 补全映射 ─────────────────────
        player_name_map = {}
        # content["lineup"] 键存在但值为 None 时(该场比赛 FotMob 未提供阵容,
        # 真实例子 match_id=4404667),.get(...,{}) 拿不到默认值——键存在就直接
        # 返回 None,再 .get 就崩;用 `or {}` 兜底 None 与缺键两种情况。
        lineup_raw = (content.get("lineup") or {}).get("lineup", [])
        for team_lineup in lineup_raw:
            for group in ["players", "bench"]:
                for p in team_lineup.get(group, []):
                    pid  = p.get("id") or p.get("playerId")
                    name = p.get("name") or p.get("shortName") or p.get("longName")
                    if pid and name:
                        player_name_map[str(pid)] = name

        records = []
        for p in players_raw:
            player_id = p.get("id") or p.get("playerId")
            team_id   = p.get("teamId")
            if not player_id or not team_id:
                continue

            stats_raw = p.get("stats", {})
            if isinstance(stats_raw, list):
                stats = _build_stat_lookup(stats_raw)
            else:
                stats = stats_raw

            # ── Rating ──────────────────────────────────────────────────
            rating_val = stats.get("rating_title") if isinstance(stats, dict) else None
            if rating_val is None:
                rating_raw = p.get("rating", {})
                if isinstance(rating_raw, dict):
                    rating_val = rating_raw.get("num") or rating_raw.get("value")
                elif isinstance(rating_raw, (int, float)):
                    rating_val = rating_raw or None

            # ── 球员姓名 ─────────────────────────────────────────────────
            name_raw = p.get("name", {})
            if isinstance(name_raw, str):
                player_name = name_raw or None
            elif isinstance(name_raw, dict):
                player_name = (
                    name_raw.get("fullName")
                    or name_raw.get("shortName")
                    or name_raw.get("lastName")
                )
            else:
                player_name = None

            # 用 lineup name map 补全
            if not player_name:
                player_name = player_name_map.get(str(player_id))

            # ── 上场时间 ──────────────────────────────────────────────────
            minutes_played = (
                p.get("minutesPlayed")
                or (stats.get("minutes_played") if isinstance(stats, dict) else None)
                or _get_stat_value(stats, "minutesPlayed", "minutePlayed", "minsPlayed")
            )
            if not minutes_played and is_lineup_fallback:
                if p.get("_is_starter"):
                    minutes_played = 90
                else:
                    continue
            if not minutes_played:
                continue

            g = lambda *keys: _get_stat_value(stats, *keys)

            rec = {
                "Match_ID":             int(match_id),
                "Player_ID":            str(player_id),
                "Player_Opta_ID":       p.get("optaId"),
                "Team_ID":              team_id,
                "is_goalkeeper":        1 if p.get("isGoalkeeper") else 0,
                "is_captain":           1 if p.get("isCaptain") else 0,
                "shirt_number":         str(p.get("shirtNumber", "")) or None,
                "position_id":          p.get("positionId"),
                "usual_position":       p.get("usualPosition"),
                "rating_title":         rating_val,
                "minutes_played":       int(minutes_played),
                "player_name":          player_name,

                # 以下别名列表统一「真实 raw key 优先，旧驼峰猜测殿后」——
                # 实测比对发现 FotMob 原始 stat.key 本身就是下划线格式（与目标列名
                # 几乎一致），旧代码只写了驼峰猜测，导致这批字段无论哪个赛季全是
                # NULL（不是老赛季数据缺失，是键名从未对上）。驼峰猜测继续保留
                # 作为兜底别名，防止未来某个赛季/端点真的换回驼峰格式。

                # ── 进攻 ──────────────────────────────────────────────
                "goals":                          g("goals"),
                "assists":                        g("assists"),
                "expected_goals":                 g("expected_goals", "expectedGoals"),
                "expected_assists":               g("expected_assists", "expectedAssists"),
                "xg_and_xa":                      g("xg_and_xa", "xgAndXa"),
                "expected_goals_on_target_variant": g("expected_goals_on_target_variant", "expectedGoalsOnTarget"),
                "expected_goals_non_penalty":     g("expected_goals_non_penalty", "expectedGoalsNonPenalty"),
                "ShotsOnTarget":                  g("ShotsOnTarget", "shotsOnTarget", "onTarget"),
                "ShotsOffTarget":                 g("ShotsOffTarget", "shotsOffTarget", "offTarget"),
                "shot_accuracy":                  g("shot_accuracy", "shotAccuracy"),
                "blocked_shots":                  g("blocked_shots", "blockedShots"),
                "big_chance_missed_title":        g("big_chance_missed_title", "bigChanceMissed"),
                "shots_woodwork":                 g("shots_woodwork", "shotsWoodwork", "hitWoodwork"),
                "missed_penalty":                 g("missed_penalty", "missedPenalty"),
                "shotmap":                        g("shotmap", "shots"),

                # ── 传球 / 创机 ────────────────────────────────────────
                "accurate_passes":                g("accurate_passes", "accuratePasses"),
                "chances_created":                g("chances_created", "chancesCreated"),
                "big_chance_created_team_title":  g("big_chance_created_team_title", "bigChanceCreated", "bigChanceCreatedTeamTitle"),
                "passes_into_final_third":        g("passes_into_final_third", "passesIntoFinalThird"),
                "accurate_crosses":               g("accurate_crosses", "accurateCrosses"),
                "long_balls_accurate":            g("long_balls_accurate", "longBallsAccurate"),

                # ── 持球 ──────────────────────────────────────────────
                "touches":                        g("touches"),
                "touches_opp_box":                g("touches_opp_box", "touchesOppBox"),
                "dispossessed":                   g("dispossessed"),
                "dribbles_succeeded":             g("dribbles_succeeded", "dribbles", "dribbleSucceeded", "dribbleWon"),

                # ── 防守 ──────────────────────────────────────────────
                "matchstats.headers.tackles":     g("matchstats.headers.tackles", "tackles"),
                "shot_blocks":                    g("shot_blocks", "shotBlocks"),
                "clearances":                     g("clearances"),
                "headed_clearance":               g("headed_clearance", "headedClearance"),
                "interceptions":                  g("interceptions"),
                "recoveries":                     g("recoveries"),
                "dribbled_past":                  g("dribbled_past", "dribbledPast"),
                "ground_duels_won":               g("ground_duels_won", "groundDuelsWon"),
                "aerials_won":                    g("aerials_won", "aerialsWon"),
                "defensive_actions":              g("defensive_actions", "defensiveActions"),
                "last_man_tackle":                g("last_man_tackle", "lastManTackle"),
                "clearance_off_the_line":         g("clearance_off_the_line", "clearanceOffTheLine"),

                # ── 对抗 / 犯规 ────────────────────────────────────────
                "duel_won":                       g("duel_won", "duelWon"),
                "duel_lost":                      g("duel_lost", "duelLost"),
                "fouls":                          g("fouls"),
                "was_fouled":                     g("was_fouled", "wasFouled"),
                "penalties_won":                  g("penalties_won", "penaltiesWon"),
                "conceded_penalties":             g("conceded_penalties", "concededPenalties"),
                "errors_led_to_goal":             g("errors_led_to_goal", "errorsLedToGoal"),

                # ── 其他 ──────────────────────────────────────────────
                "corners":                        g("corners"),
                "Offsides":                       g("Offsides", "offsides"),
                "owngoal":                        g("owngoal", "ownGoal"),
                "fantasy_points":                 g("fantasy_points", "fantasyPoints"),

                # ── 门将 ──────────────────────────────────────────────
                "saves":                          g("saves"),
                "goals_conceded":                 g("goals_conceded", "goalsConceded"),
                "expected_goals_on_target_faced": g("expected_goals_on_target_faced", "expectedGoalsOnTargetFaced"),
                "goals_prevented":                g("goals_prevented", "goalsPrevented"),
                "keeper_diving_save":             g("keeper_diving_save", "keeperDivingSave"),
                "saves_inside_box":               g("saves_inside_box", "savesInsideBox"),
                "keeper_sweeper":                 g("keeper_sweeper", "keeperSweeper"),
                "punches":                        g("punches"),
                "player_throws":                  g("player_throws", "playerThrows"),
                "keeper_high_claim":              g("keeper_high_claim", "keeperHighClaim"),
                "saved_penalties":                g("saved_penalties", "savedPenalties"),
                "saved_penalties_in_shootout":    g("saved_penalties_in_shootout", "savedPenaltiesInShootout"),

                # ── 体能 ──────────────────────────────────────────────
                # 实测未在样本比赛中出现（不确定是本字段全站未采集还是仅该场
                # 无数据），驼峰猜测暂保留，未额外加下划线别名——避免在没有
                # 真实证据支撑的情况下臆造新的错误映射。
                "physical_metrics_topspeed":          g("topSpeed"),
                "physical_metrics_distance_covered":  g("distanceCovered"),
                "physical_metrics_walking":           g("walking"),
                "physical_metrics_running":           g("running"),
                "physical_metrics_sprinting":         g("sprinting"),
                "physical_metrics_number_of_sprints": g("numberOfSprints"),
            }

            records.append(rec)

        if records:
            log.info("[PlayerStats] 解析到 %d 名球员", len(records))
        return records

    def parse_match_events(
        self,
        page_props: dict,
        match_id: Union[int, str],
    ) -> List[dict]:
        """
        从 pageProps 中提取 fact_match_events 表所需的所有字段（比赛事件时间线）。

        来源: content.matchFacts.events.events[]（按数组顺序，一行一事件）
        event_type ∈ {Goal, Card, Substitution, AddedTime, Half}，不同类型只有
        各自相关字段有值，其余为 None。每条记录附带 "extra" 字典（调用方决定是否
        序列化进 extra_json），保留 shotmapEvent 等未建核心列的嵌套细节。
        """
        content = page_props.get("content", {})
        events_raw = (content.get("matchFacts", {}) or {}).get("events", {}) or {}
        events_list = events_raw.get("events", []) or []

        # 已显式映射到核心列的原始 key，其余原样进 extra
        mapped_keys = {
            "type", "time", "overloadTime", "isHome", "homeScore", "awayScore",
            "newScore", "playerId", "player", "nameStr", "fullName", "card",
            "assistPlayerId", "assistInput", "swap", "minutesAddedInput", "eventId",
        }

        records = []
        for idx, ev in enumerate(events_list):
            player = ev.get("player") or {}
            player_id = ev.get("playerId") or player.get("id")
            player_name = ev.get("nameStr") or ev.get("fullName") or player.get("name")

            # 换人：swap = [进场球员, 出场球员]
            sub_in_id = sub_in_name = sub_out_id = sub_out_name = None
            swap = ev.get("swap")
            if isinstance(swap, list) and len(swap) == 2:
                sub_in_id, sub_in_name = swap[0].get("id"), swap[0].get("name")
                sub_out_id, sub_out_name = swap[1].get("id"), swap[1].get("name")

            # 比分：Goal 事件的 homeScore/awayScore 是"进球发生前"的比分（FotMob
            # 自身的字段语义如此，非解析错误），真正的"进球后"比分在 newScore
            # 字段里（[home, away]）。非 Goal 事件没有 newScore（此时也不需要，
            # 因为没有进球发生，homeScore/awayScore 本来就是当前比分）。
            new_score = ev.get("newScore")
            if isinstance(new_score, list) and len(new_score) == 2:
                home_score, away_score = new_score[0], new_score[1]
            else:
                home_score, away_score = ev.get("homeScore"), ev.get("awayScore")

            extra = {k: v for k, v in ev.items() if k not in mapped_keys}

            records.append({
                "Match_ID":            int(match_id),
                "event_index":         idx,
                "event_type":          ev.get("type"),
                "minute":              ev.get("time"),
                "overload_time":       ev.get("overloadTime"),
                "is_home":             (1 if ev.get("isHome") else 0) if "isHome" in ev else None,
                "home_score":          home_score,
                "away_score":          away_score,
                "player_id":           str(player_id) if player_id else None,
                "player_name":         player_name,
                "card_type":           ev.get("card"),
                "assist_player_id":    str(ev.get("assistPlayerId")) if ev.get("assistPlayerId") else None,
                "assist_player_name":  ev.get("assistInput"),
                "sub_in_player_id":    str(sub_in_id) if sub_in_id else None,
                "sub_in_player_name":  sub_in_name,
                "sub_out_player_id":   str(sub_out_id) if sub_out_id else None,
                "sub_out_player_name": sub_out_name,
                "minutes_added":       ev.get("minutesAddedInput"),
                "event_id":            str(ev.get("eventId")) if ev.get("eventId") else None,
                "extra":               extra,
            })

        return records

    def parse_lineup_records(
        self,
        page_props: dict,
        match_id: Union[int, str],
    ) -> List[dict]:
        """
        从 pageProps 中提取 fact_match_lineup 表所需的所有字段（阵容与阵型）。

        来源: content.lineup.{homeTeam,awayTeam}.{formation,starters[],subs[]}
        只含出场名单（首发 starters + 替补 subs），不含教练/伤停名单
        （content.lineup.{home,away}Team.{coach,unavailable} 不解析）。
        每条记录附带 "extra" 字典（调用方决定是否序列化进 extra_json）。
        """
        content = page_props.get("content", {})
        lineup = content.get("lineup", {}) or {}

        mapped_keys = {
            "id", "name", "positionId", "usualPlayingPositionId",
            "shirtNumber", "countryCode", "marketValue", "performance",
            "isCaptain",
        }

        records = []
        for is_home, side_key in ((True, "homeTeam"), (False, "awayTeam")):
            side = lineup.get(side_key) or {}
            team_id = side.get("id")
            formation = side.get("formation")

            for group, is_starter in (("starters", 1), ("subs", 0)):
                for p in side.get(group, []) or []:
                    perf = p.get("performance") or {}
                    sub_in_time = sub_out_time = None
                    for sub_ev in perf.get("substitutionEvents", []) or []:
                        if sub_ev.get("type") == "subIn":
                            sub_in_time = sub_ev.get("time")
                        elif sub_ev.get("type") == "subOut":
                            sub_out_time = sub_ev.get("time")

                    extra = {k: v for k, v in p.items() if k not in mapped_keys}
                    extra["performance"] = perf

                    records.append({
                        "Match_ID":          int(match_id),
                        "Team_ID":           team_id,
                        "is_home":           1 if is_home else 0,
                        "formation":         formation,
                        "Player_ID":         str(p.get("id")),
                        "player_name":       p.get("name"),
                        "shirt_number":      p.get("shirtNumber"),
                        "position_id":       p.get("positionId"),
                        "usual_position_id": p.get("usualPlayingPositionId"),
                        "is_starter":        is_starter,
                        "is_captain":        1 if p.get("isCaptain") else 0,
                        "country_code":      p.get("countryCode"),
                        "market_value":      p.get("marketValue"),
                        "rating":            perf.get("rating"),
                        "sub_in_time":       sub_in_time,
                        "sub_out_time":      sub_out_time,
                        "extra":             extra,
                    })

        return records

    def parse_league_table(
        self,
        league_data: dict,
        league_id: Union[int, str],
        season: str,
    ) -> List[dict]:
        """
        从 league_matches() 返回的原始 JSON 提取 fact_league_table 表所需字段（联赛积分榜）。

        来源: league_data.table[0].data.table.{all,home,away,form,xg}
        每个分档(table_type)下是一份球队列表；xg 档额外含
        xg/xg_conceded/x_points/x_position，其它档这四列为 None。
        """
        data_node = ((league_data.get("table") or [{}])[0].get("data", {}) or {})
        table_data = data_node.get("table", {}) or {}
        if not table_data:
            # 分组赛制赛季(如 K1 常规轮+冠军/保级组、J联赛 2026 过渡期东西分组)
            # 没有 data.table,只有 data.tables[]——每个子表各带 leagueName 与
            # 内层 table.{all,home,away,xg}。与主联赛同名的子表是总表,按普通
            # table_type 发出(standings 查询消费 'all');分组子表的 table_type
            # 加 ":组名" 后缀保留数据,绝不与总表混在同一 'all' 里(位次会互相
            # 冲突)。没有总表的赛季(J1 2026 过渡赛)诚实地只有分组行。
            # 名称比较必须归一化:真实响应里顶层 leagueName 与子表写法存在
            # "K League 1" vs "K-League 1" 这类连字符/空格差异,严格相等会把
            # 总表误判成分组表(2026-08-07 真实踩坑)。
            def _norm(name) -> str:
                return "".join(ch for ch in str(name or "").lower() if ch.isalnum())

            main_name = _norm(data_node.get("leagueName"))
            merged: dict = {}
            for sub in data_node.get("tables") or []:
                if not isinstance(sub, dict):
                    continue
                sub_name = str(sub.get("leagueName") or "").strip()
                inner = sub.get("table") or {}
                if not isinstance(inner, dict):
                    continue
                suffix = "" if _norm(sub_name) == main_name else f":{sub_name}"
                for key, teams in inner.items():
                    merged[f"{key}{suffix}"] = teams
            table_data = merged

        mapped_keys = {
            "name", "shortName", "id", "pageUrl", "deduction", "ongoing",
            "played", "wins", "draws", "losses", "scoresStr", "goalConDiff",
            "pts", "idx", "qualColor", "teamId", "teamName", "xg", "xgConceded",
            "xPoints", "position", "xPosition", "goalsScored", "featuredInMatch",
        }

        records = []
        for table_type, teams in table_data.items():
            if not isinstance(teams, list):
                continue
            for t in teams:
                team_id = t.get("id") or t.get("teamId")
                goals_for, goals_against = _split_scores_str(t.get("scoresStr"))
                extra = {k: v for k, v in t.items() if k not in mapped_keys}

                records.append({
                    "League_ID":     int(league_id),
                    "Season":        season,
                    "table_type":    table_type,
                    "Team_ID":       team_id,
                    "Team_Name":     t.get("name") or t.get("teamName"),
                    "position":      t.get("idx") if t.get("idx") is not None else t.get("position"),
                    "played":        t.get("played"),
                    "wins":          t.get("wins"),
                    "draws":         t.get("draws"),
                    "losses":        t.get("losses"),
                    "goals_for":     goals_for,
                    "goals_against": goals_against,
                    "goal_diff":     t.get("goalConDiff"),
                    "points":        t.get("pts"),
                    "deduction":     t.get("deduction"),
                    "qual_color":    t.get("qualColor"),
                    "xg":            t.get("xg"),
                    "xg_conceded":   t.get("xgConceded"),
                    "x_points":      t.get("xPoints"),
                    "x_position":    t.get("xPosition"),
                    "extra":         extra,
                })

        return records

    def fetch_stat_leaderboard(self, url: str) -> dict:
        """
        抓取联赛某统计维度的全量球员榜单 JSON。
        url 来自 league_matches() 返回的 stats.players[].fetchAllUrl，
        域名为 data.fotmob.com，非 SSR 页面，直接 GET 即为 JSON。

        实测返回结构:
          {"TopLists": [{"StatName": "goals", "StatList": [
              {"ParticipantName", "ParticiantId"(FotMob 原始拼写少一个 p),
               "TeamId", "TeamName", "StatValue", "SubStatValue",
               "MinutesPlayed", "MatchesPlayed", "StatValueCount", "Rank",
               "ParticipantCountryCode", "Positions", "TeamColor"}, ...]}],
           "LeagueName": "..."}
        """
        r = self._get(url, headers={"Accept": "application/json"})
        return self._decode_json_response(r, "fetch_stat_leaderboard")

    def parse_season_player_stats(
        self,
        league_data: dict,
        league_id: Union[int, str],
        season: str,
    ) -> List[dict]:
        """
        遍历 league_data.stats.players[] 里的每个统计维度，用其 fetchAllUrl 抓取
        全量榜单（而非 topThree），提取 fact_season_player_stats 表所需字段。
        单个维度抓取失败不影响其它维度（记录 warning 并跳过）。
        """
        stat_defs = (league_data.get("stats") or {}).get("players") or []

        mapped_keys = {
            "ParticipantName", "ParticiantId", "TeamId", "TeamName",
            "StatValue", "Rank",
        }

        records = []
        for stat_def in stat_defs:
            url = stat_def.get("fetchAllUrl")
            stat_name = stat_def.get("name")
            if not url or not stat_name:
                continue
            try:
                data = self.fetch_stat_leaderboard(url)
            except Exception as exc:
                exception_class = _safe_exception_class_name(exc)
                log.warning(
                    "[SeasonPlayerStats] fetch_stat_leaderboard failed "
                    "(%s); skipping dimension",
                    exception_class,
                )
                continue

            top_lists = data.get("TopLists") or []
            stat_list = top_lists[0].get("StatList", []) if top_lists else []

            for row in stat_list:
                extra = {k: v for k, v in row.items() if k not in mapped_keys}
                records.append({
                    "League_ID":   int(league_id),
                    "Season":      season,
                    "stat_name":   stat_name,
                    "Player_ID":   str(row.get("ParticiantId")),
                    "Player_Name": row.get("ParticipantName"),
                    "Team_ID":     row.get("TeamId"),
                    "Team_Name":   row.get("TeamName"),
                    "rank":        row.get("Rank"),
                    "value":       row.get("StatValue"),
                    "extra":       extra,
                })

        return records


# ─────────────────────────────────────────────────────────────────────────────
# 命令行测试
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    client = FotMobClient()

    # 1. 验证代理 IP
    print("=" * 60)
    print("🔍 验证代理 IP")
    ip_info = client.check_ip()
    print(f"   代理出口 IP: {ip_info.get('origin', '?')}")

    # 2. 获取今日比赛
    print("\n" + "=" * 60)
    today = time.strftime("%Y%m%d")
    print(f"📅 获取今日比赛 ({today})")
    matches = client.daily_matches(today)
    total = sum(len(lg.get("matches", [])) for lg in matches.get("leagues", []))
    print(f"   {len(matches.get('leagues', []))} 联赛, {total} 场比赛")
    for lg in matches.get("leagues", [])[:5]:
        print(f"   · {lg.get('name', '?')}: {len(lg.get('matches', []))} 场")

    # 3. 获取比赛详情 + DB-ready 解析
    print("\n" + "=" * 60)
    test_id = 4193490
    print(f"⚽ 获取比赛详情 (matchId={test_id})")
    pp = client.match_details(test_id)
    g = pp["general"]
    print(f"   {g['homeTeam']['name']} vs {g['awayTeam']['name']}")
    content = pp["content"]
    lu = content.get("lineup", {})
    sm = content.get("shotmap", {})
    ps = content.get("playerStats", {})
    print(f"   阵容: {'✅' if lu.get('homeTeam') else '❌'}")
    print(f"   射门图: {len(sm.get('shots', []))} shots")
    print(f"   球员统计: {len(ps)} players")

    # 4. 测试 DB-ready 解析方法
    print("\n" + "=" * 60)
    print("🗄️  测试 DB-ready 解析方法")

    dim = client.parse_match_dim(pp, test_id, league_id=47, date="2026-04-11")
    print(f"   dim_match: Home_Team_ID={dim['Home_Team_ID']}, Away_Team_ID={dim['Away_Team_ID']}, "
          f"Score={dim['home_score']}-{dim['away_score']}, Referee={dim['Referee']}, Round={dim['Match_Round']}")

    shots = client.parse_shotmap_records(pp, test_id)
    if shots:
        s0 = shots[0]
        print(f"   fact_shotmap[0]: Player={s0['Player_ID']}, Team={s0['Team_ID']}, "
              f"Min={s0['Minute']}, Period={s0['Period']}, xG={s0['xG']}, "
              f"xGOT={s0['xGOT']}, Situation={s0['Situation']}, Type={s0['Shot_Type']}")
    print(f"   fact_shotmap: {len(shots)} 条记录")

    t_stats = client.parse_team_stats_records(pp, test_id)
    print(f"   fact_team_match_stats: {len(t_stats)} 条记录 (含各半场)")

    p_records = client.parse_player_stats_records(pp, test_id)
    if p_records:
        p0 = p_records[0]
        print(f"   fact_player_match_stats[0]: {p0['player_name']} "
              f"(ID={p0['Player_ID']}, Team={p0['Team_ID']}, "
              f"Goals={p0['goals']}, xG={p0['expected_goals']}, Rating={p0['rating_title']})")
    print(f"   fact_player_match_stats: {len(p_records)} 条记录")

    print("\n✅ FotMob 客户端全部测试通过！")
