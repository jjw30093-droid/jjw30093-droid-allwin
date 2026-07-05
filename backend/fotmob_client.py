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
import re
import time
import random
import logging
from typing import Optional, Union, List, Dict, Any

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ThorData 住宅代理（轮换会话，每次请求自动换 IP）— 凭证从 .env 读取
# ─────────────────────────────────────────────────────────────────────────────
THORDATA_PROXY = os.environ["THORDATA_PROXY"]

PROXIES = {"http": THORDATA_PROXY, "https": THORDATA_PROXY}

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


class FotMobClient:
    """无浏览器 FotMob 数据客户端"""

    def __init__(
        self,
        proxy: str = THORDATA_PROXY,
        impersonate: str = "chrome131",
        timeout: int = 25,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.proxy = proxy
        self.proxies = {"http": proxy, "https": proxy} if proxy else {}
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    # ─────────────────────────────────────────────────────────────────────
    # 内部：带重试的请求
    # ─────────────────────────────────────────────────────────────────────
    def _get(self, url: str, headers: Optional[dict] = None) -> cffi_requests.Response:
        hdrs = {**DEFAULT_HEADERS, **(headers or {})}
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = cffi_requests.get(
                    url,
                    headers=hdrs,
                    proxies=self.proxies,
                    impersonate=self.impersonate,
                    timeout=self.timeout,
                )
                if r.status_code == 200:
                    return r
                if r.status_code == 403:
                    log.warning(
                        "[FotMob] 403 on %s (attempt %d/%d) — %s",
                        url, attempt, self.max_retries, r.text[:120],
                    )
                else:
                    log.warning(
                        "[FotMob] HTTP %d on %s (attempt %d/%d)",
                        r.status_code, url, attempt, self.max_retries,
                    )
                last_err = Exception(f"HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                log.warning(
                    "[FotMob] Request error on %s (attempt %d/%d): %s",
                    url, attempt, self.max_retries, e,
                )
                last_err = e

            if attempt < self.max_retries:
                sleep = self.retry_delay + random.uniform(0.5, 1.5)
                time.sleep(sleep)

        raise last_err or Exception(f"Failed after {self.max_retries} retries: {url}")

    # ─────────────────────────────────────────────────────────────────────
    # 内部：从 SSR HTML 提取 __NEXT_DATA__
    # ─────────────────────────────────────────────────────────────────────
    def _extract_next_data(self, html: str) -> dict:
        m = _NEXT_DATA_RE.search(html)
        if not m:
            raise ValueError("__NEXT_DATA__ not found in HTML")
        return json.loads(m.group(1))

    # ─────────────────────────────────────────────────────────────────────
    # 公开 API — 原始数据
    # ─────────────────────────────────────────────────────────────────────

    def check_ip(self) -> dict:
        """验证代理 IP 是否正常工作，返回 IP 信息"""
        r = cffi_requests.get(
            "https://httpbin.org/ip",
            proxies=self.proxies,
            impersonate=self.impersonate,
            timeout=10,
        )
        return r.json()

    def daily_matches(self, date: str) -> dict:
        """
        获取某天的比赛列表
        date 格式: "20260411"
        返回: {"leagues": [...], "date": "..."}
        """
        url = f"https://www.fotmob.com/api/data/matches?date={date}&timezone=Asia%2FShanghai&ccode3=JPN"
        r = self._get(url, headers={"Accept": "application/json"})
        return r.json()

    def match_details(self, match_id: Union[int, str]) -> dict:
        """
        获取比赛详情（通过 SSR 页面绕过 Turnstile）
        返回 pageProps: {general, header, content, ...}
        content 包含: lineup, shotmap, stats, playerStats, h2h, momentum 等

        注意：SSR 页面有 CDN 缓存（5~20 分钟），赛前首发确认阶段会有轻微滞后。
        """
        url = f"https://www.fotmob.com/match/{match_id}"
        r = self._get(url, headers={"Accept": "text/html"})
        nd = self._extract_next_data(r.text)
        page_props = nd.get("props", {}).get("pageProps", {})
        if not page_props or not page_props.get("general"):
            raise ValueError(
                f"Empty pageProps for match {match_id} — "
                f"possible Cloudflare block or invalid match ID"
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
        return r.json()

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
          Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,
          home_score, away_score, status,
          Temperature, Wind_Speed, Referee, Match_Round, Who_Lost_On_Penalties
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
        status_obj = header.get("status", {})
        score_str  = status_obj.get("scoreStr", "")
        try:
            parts    = score_str.split(" - ")
            h_score  = int(parts[0].strip())
            a_score  = int(parts[1].strip())
        except Exception:
            h_score = home_team.get("score", 0)
            a_score = away_team.get("score", 0)
            try:
                h_score = int(h_score or 0)
                a_score = int(a_score or 0)
            except Exception:
                h_score = a_score = 0

        # ── 比赛状态 ─────────────────────────────────────────────────────
        match_status = "Finish" if status_obj.get("finished") else status_obj.get("reason", {}).get("short", "Unknown")

        # ── 联赛 ID / 日期（来自调用方，或从 general 推断）──────────────
        if league_id is None:
            league_id = general.get("leagueId") or general.get("parentLeagueId")
        if date is None:
            # general.matchTimeUTC 格式如 "2026-04-11T14:00:00.000Z"
            utc_time = general.get("matchTimeUTC", "")
            date = utc_time.split("T")[0] if "T" in utc_time else utc_time[:10]

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
        weather_raw = content.get("weather") or general.get("weather")
        if isinstance(weather_raw, dict):
            temperature = weather_raw.get("temperature") or weather_raw.get("temp")
            wind_speed  = weather_raw.get("windSpeed") or weather_raw.get("wind")
            # FotMob 有时把天气包在 .conditions 里
            cond = weather_raw.get("conditions", {})
            if isinstance(cond, dict):
                temperature = temperature or cond.get("temperature") or cond.get("temp")
                wind_speed  = wind_speed  or cond.get("windSpeed")

        # ── 点球大战失利方 ───────────────────────────────────────────────
        who_lost_penalties = None
        # general.penaltiesInfo 或 general.penaltyScore
        penalties_info = general.get("penaltiesInfo") or general.get("penaltyScore")
        if isinstance(penalties_info, dict):
            loser = penalties_info.get("loser") or penalties_info.get("whoLostOnPenalties")
            who_lost_penalties = str(loser) if loser is not None else None
        elif general.get("whoLostOnPenalties"):
            who_lost_penalties = str(general["whoLostOnPenalties"])

        return {
            "Match_ID":             int(match_id),
            "Season":               season,
            "League_ID":            league_id,
            "Date":                 date,
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
          Situation, Outcome, Shot_Type

        说明:
          - Situation: 射门情境（RegularPlay/FromCorner/SetPiece/FreeKick/Penalty）
            FotMob SSR 数据中该字段有时缺失（旧场次），需后续补采
          - xGOT:      expectedGoalsOnTarget，SSR 数据中有时缺失
          - Shot_Type: 射门方式（LeftFoot/RightFoot/Head/OtherBodyPart）
          - Period:    比赛阶段（FirstHalf/SecondHalf/ExtraTimeFirstHalf 等）
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
                "Outcome":   s.get("eventType"),   # AttemptSaved/Goal/Miss/BlockedShot
                "Shot_Type": s.get("shotType"),    # LeftFoot/RightFoot/Head 等
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

        status_obj = header.get("status", {})
        score_str  = status_obj.get("scoreStr", "")
        try:
            parts   = score_str.split(" - ")
            h_score = int(parts[0].strip())
            a_score = int(parts[1].strip())
        except Exception:
            h_score = int(home_team.get("score", 0) or 0)
            a_score = int(away_team.get("score", 0) or 0)

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
                "Goals":    h_score if period_name == "All" else home_team.get("score", 0),
            }
            as_: Dict[str, Any] = {
                "Match_ID": int(match_id),
                "Team_ID":  aid,
                "Period":   period_name,
                "Goals":    a_score if period_name == "All" else away_team.get("score", 0),
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
        lineup_raw = content.get("lineup", {}).get("lineup", [])
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

                # ── 进攻 ──────────────────────────────────────────────
                "goals":                          g("goals"),
                "assists":                        g("assists"),
                "expected_goals":                 g("expectedGoals"),
                "expected_assists":               g("expectedAssists"),
                "xg_and_xa":                      g("xgAndXa", "xg_and_xa"),
                "expected_goals_on_target_variant": g("expectedGoalsOnTarget"),
                "expected_goals_non_penalty":     g("expectedGoalsNonPenalty"),
                "ShotsOnTarget":                  g("shotsOnTarget", "onTarget"),
                "ShotsOffTarget":                 g("shotsOffTarget", "offTarget"),
                "shot_accuracy":                  g("shotAccuracy"),
                "blocked_shots":                  g("blockedShots"),
                "big_chance_missed_title":        g("bigChanceMissed"),
                "shots_woodwork":                 g("shotsWoodwork", "hitWoodwork"),
                "missed_penalty":                 g("missedPenalty"),
                "shotmap":                        g("shotmap", "shots"),

                # ── 传球 / 创机 ────────────────────────────────────────
                "accurate_passes":                g("accuratePasses"),
                "chances_created":                g("chancesCreated"),
                "big_chance_created_team_title":  g("bigChanceCreated", "bigChanceCreatedTeamTitle"),
                "passes_into_final_third":        g("passesIntoFinalThird"),
                "accurate_crosses":               g("accurateCrosses"),
                "long_balls_accurate":            g("longBallsAccurate"),

                # ── 持球 ──────────────────────────────────────────────
                "touches":                        g("touches"),
                "touches_opp_box":                g("touchesOppBox"),
                "dispossessed":                   g("dispossessed"),
                "dribbles_succeeded":             g("dribbles", "dribbleSucceeded", "dribbleWon"),

                # ── 防守 ──────────────────────────────────────────────
                "matchstats.headers.tackles":     g("tackles"),
                "shot_blocks":                    g("shotBlocks"),
                "clearances":                     g("clearances"),
                "headed_clearance":               g("headedClearance"),
                "interceptions":                  g("interceptions"),
                "recoveries":                     g("recoveries"),
                "dribbled_past":                  g("dribbledPast"),
                "ground_duels_won":               g("groundDuelsWon"),
                "aerials_won":                    g("aerialsWon"),
                "defensive_actions":              g("defensiveActions"),
                "last_man_tackle":                g("lastManTackle"),
                "clearance_off_the_line":         g("clearanceOffTheLine"),

                # ── 对抗 / 犯规 ────────────────────────────────────────
                "duel_won":                       g("duelWon"),
                "duel_lost":                      g("duelLost"),
                "fouls":                          g("fouls"),
                "was_fouled":                     g("wasFouled"),
                "penalties_won":                  g("penaltiesWon"),
                "conceded_penalties":             g("concededPenalties"),
                "errors_led_to_goal":             g("errorsLedToGoal"),

                # ── 其他 ──────────────────────────────────────────────
                "corners":                        g("corners"),
                "Offsides":                       g("offsides", "Offsides"),
                "owngoal":                        g("ownGoal", "owngoal"),
                "fantasy_points":                 g("fantasyPoints"),

                # ── 门将 ──────────────────────────────────────────────
                "saves":                          g("saves"),
                "goals_conceded":                 g("goalsConceded"),
                "expected_goals_on_target_faced": g("expectedGoalsOnTargetFaced"),
                "goals_prevented":                g("goalsPrevented"),
                "keeper_diving_save":             g("keeperDivingSave"),
                "saves_inside_box":               g("savesInsideBox"),
                "keeper_sweeper":                 g("keeperSweeper"),
                "punches":                        g("punches"),
                "player_throws":                  g("playerThrows"),
                "keeper_high_claim":              g("keeperHighClaim"),
                "saved_penalties":                g("savedPenalties"),
                "saved_penalties_in_shootout":    g("savedPenaltiesInShootout"),

                # ── 体能 ──────────────────────────────────────────────
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
