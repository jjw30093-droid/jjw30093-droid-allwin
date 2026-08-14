"""NowGoal Provider Adapter(纯解析 + 网络获取分离)。

来源能力(按旧项目 miaomiaodi.vip 代码审计,见 docs/current-state.md §4,真实端点 UNVERIFIED):
- 日程:SoccerAjax type=6,响应 Data 字段按行,`A[` 开头的行是比赛;
  按单引号 split 后 parts[0] 含 titan_id,parts[1]=主队名,parts[3]=客队名,
  行内还含开球时间字段(保留原始行以便提取)。
- 赔率:soccerajax type=14,每公司仅 f(初盘)/l(最新)两组快照,没有完整时间序列。
  市场 euro(1x2: u=主 g=平 d=客)/ ah(u=上/主 g=盘口线 d=下/客)/ ou(同形)。
- 主客反转:1x2 交换 home/away;AH 交换双边并把盘口线取负;OU 不换(对称)。
- 公司优先级:CID 8(Bet365)、31(Sbobet),否则第一家有效公司。
- WAF:响应含 "Just a moment"/cloudflare 即视为被挡。

时间戳纪律(CLAUDE.md §6.2):NowGoal 不声明来源更新时间 → source_updated_at 恒 NULL,
不得用抓取时间伪装;observed_at / ingested_at 由 ingest 层负责。
"""

import json
import os
import random
import re
from datetime import datetime

BASE_URL = "https://www.nowgoal26.com"
SCHEDULE_URL = BASE_URL + "/ajax/SoccerAjax"
ODDS_URL = BASE_URL + "/ajax/soccerajax"

DEFAULT_TARGET_CIDS = ("8", "1", "3")   # Bet365 / 澳门 Macauslot / 皇冠 Crown
# (数据管道重建 Phase 4:三家均在同一 type=14 面板、一次请求全拿到、三市场齐全,
#  Phase 0 实测 3/3 场 present。原第二家 Sbobet(31)已换为用户指定的澳门+皇冠。)

FETCH_TIMEOUT_SECONDS = 15.0

_WAF_MARKERS = ("just a moment", "cloudflare", "cf-challenge", "attention required")

# 行内开球时间(备选格式):"2026-07-19 19:30" / "2026-07-19T19:30"
_KICKOFF_RE = re.compile(r"((?:\d{4}-)?\d{1,2}-\d{1,2})[ T](\d{1,2}:\d{2})")

# 真实 probe(2026-07-19,HTTP 200)观察到的开球时间格式:引号内 JS Date 构造元组
# '2026,6,18,16,00,00'(月份 0 基,JS 惯例;按 probe 样例与查询日期对齐推断)
_JS_DATE_RE = re.compile(r"^(\d{4}),(\d{1,2}),(\d{1,2}),(\d{1,2}),(\d{1,2}),(\d{1,2})$")

# 真实 probe 观察:A 行形如 A[1]=[2912838,3,505,498,'Molde','Brann',...],
# titan_id 是 "=[" 后第一段数字
_TITAN_AFTER_BRACKET_RE = re.compile(r"=\[(\d+)")

_INT_RE = re.compile(r"(\d+)")


class NowGoalError(Exception):
    """NowGoal 抓取/解析失败。"""


class WAFBlockedError(NowGoalError):
    """命中 WAF(Just a moment / cloudflare),调用方应跳过并退避。"""


def looks_blocked(text: str) -> bool:
    """WAF 检测:响应文本命中已知拦截页标记。"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _WAF_MARKERS)


# ── 日程解析 ─────────────────────────────────────────────────────────────


def _extract_titan_id(head: str) -> str | None:
    """从引号 split 的 parts[0] 提取 titan_id。

    真实 probe(2026-07-19)确认:A 行是 A[N]=[<titan_id>,<league>,<hid>,<aid>,'主队',...,
    titan_id 为 "=[" 后第一段数字;裸格式(无前缀)时回退取第一段数字。
    """
    m = _TITAN_AFTER_BRACKET_RE.search(head)
    if m:
        return m.group(1)
    numbers = _INT_RE.findall(head)
    return numbers[0] if numbers else None


def _extract_head_team_ids(head: str) -> tuple[str | None, str | None]:
    """提取 A 行头部的 NowGoal 主/客队 id(hid/aid,"=[" 后第 3/4 段数字)。

    格式按真实 probe:[titan_id, league_id, hid, aid, '主队', ...];
    裸格式或字段不足时返回 (None, None),不猜。
    """
    m = _TITAN_AFTER_BRACKET_RE.search(head)
    if not m:
        return (None, None)
    numbers = _INT_RE.findall(head[m.start():])
    if len(numbers) < 4:
        return (None, None)
    return (numbers[2], numbers[3])


def _extract_kickoff(parts: list[str], raw_line: str) -> str | None:
    """从行内提取开球时间(尽力而为,提不到给 None,不编造)。

    优先识别真实 probe 观察到的引号内 JS Date 元组('2026,6,18,16,00,00',月份 0 基),
    转成 "YYYY-MM-DD HH:MM:SS";备选识别破折号日期格式。
    """
    for quoted in parts[1::2]:
        m = _JS_DATE_RE.match(quoted.strip())
        if not m:
            continue
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
        try:
            return datetime(y, mo + 1, d, hh, mm, ss).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    m = _KICKOFF_RE.search(raw_line)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def parse_schedule(data_text: str) -> list[dict]:
    """解析 type=6 日程响应的 Data 文本。

    只认 `A[` 开头的行(其余是联赛/杂项行,忽略)。
    返回 [{titan_id, home_name, away_name, kickoff, raw_line}];
    titan_id / 队名任一缺失的行剔除。
    """
    rows: list[dict] = []
    for line in (data_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("A["):
            continue
        parts = stripped.split("'")
        if len(parts) < 4:
            continue
        titan_id = _extract_titan_id(parts[0])
        home_name = parts[1].strip()
        away_name = parts[3].strip()
        if not titan_id or not home_name or not away_name:
            continue
        provider_home_id, provider_away_id = _extract_head_team_ids(parts[0])
        rows.append(
            {
                "titan_id": titan_id,
                "home_name": home_name,
                "away_name": away_name,
                "provider_home_id": provider_home_id,
                "provider_away_id": provider_away_id,
                "kickoff": _extract_kickoff(parts, stripped),
                "raw_line": stripped,
            }
        )
    return rows


# ── 赔率解析 ─────────────────────────────────────────────────────────────

# NowGoal 市场名 → 本系统 canonical 市场名
_MARKET_KEYS = (("euro", "1x2"), ("ah", "ah"), ("ou", "ou"))

# 各市场 u/g/d 槽位 → canonical 字段名
_FIELD_MAP = {
    "1x2": {"u": "home", "g": "draw", "d": "away"},
    "ah": {"u": "home", "g": "line", "d": "away"},
    "ou": {"u": "over", "g": "line", "d": "under"},
}


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_group(group, market: str) -> dict | None:
    """把一组 u/g/d 赔率转成 canonical 字段 dict;任一槽位缺失/非数值 → 整组视为空。"""
    if not isinstance(group, dict):
        return None
    out = {}
    for slot, field in _FIELD_MAP[market].items():
        v = _to_float(group.get(slot))
        if v is None:
            return None
        out[field] = v
    return out


def _iter_companies(payload: dict):
    """公司列表定位:优先旧项目审计构造的 `companies`/`Data`(直接列表/字典),
    真实响应(2026-07-21 titan_id=2912218 实测,ErrCode=0)确认实际是
    `Data.mixodds`(列表,元素形如 {cid, cn, euro, ah, ou})——旧构造未预见这一层
    嵌套,故在两种形状都取不到列表时再尝试 `Data.mixodds`。"""
    companies = payload.get("companies")
    if companies is None:
        companies = payload.get("Data")
    if isinstance(companies, dict) and "mixodds" in companies:
        companies = companies.get("mixodds")
    elif isinstance(companies, dict):
        companies = list(companies.values())
    if not isinstance(companies, list):
        return []
    return [c for c in companies if isinstance(c, dict)]


def _company_records(company: dict) -> list[dict]:
    cid = str(company.get("cid") or company.get("id") or "").strip()
    if not cid:
        return []
    # 真实响应公司名字段是 'cn'(2026-07-21 titan_id=2912218 实测,cid=8 对应 cn='Bet365');
    # 'name'/'company' 是旧项目审计构造时的猜测字段名,保留作回退。
    name = str(company.get("cn") or company.get("name") or company.get("company") or "").strip()
    records = []
    for src_key, market in _MARKET_KEYS:
        block = company.get(src_key)
        if not isinstance(block, dict):
            continue
        initial = _parse_group(block.get("f"), market)
        latest = _parse_group(block.get("l"), market)
        if initial is None and latest is None:
            continue   # 空值行剔除
        records.append(
            {
                "market": market,
                "company_id": cid,
                "company_name": name,
                "initial": initial,
                "latest": latest,
            }
        )
    return records


def parse_odds(payload: dict, target_cids=DEFAULT_TARGET_CIDS) -> list[dict]:
    """解析 type=14 赔率响应为 canonical 记录列表。

    - 每 (company, market) 一条:{market, company_id, company_name, initial, latest};
    - initial/latest 为 canonical 字段 dict 或 None(该组缺失);两组全空的行剔除;
    - 公司选择:按 target_cids 顺序取全部命中的公司;缺哪家少哪家,一家都没命中 → 空
      (不回退拿别家冒充,数据管道重建 Phase 4 用户口径"没有就不抓")。
    """
    if not isinstance(payload, dict):
        return []
    by_cid: dict[str, list[dict]] = {}
    order: list[str] = []
    for company in _iter_companies(payload):
        recs = _company_records(company)
        if not recs:
            continue
        cid = recs[0]["company_id"]
        if cid not in by_cid:
            by_cid[cid] = []
            order.append(cid)
        by_cid[cid].extend(recs)

    selected: list[dict] = []
    for cid in target_cids:
        selected.extend(by_cid.get(str(cid), []))
    # 目标公司缺席就少哪家(用户要求"澳门没有就不抓"):绝不回退拿面板第一家冒充。
    # 一家都没命中 → 返回空,空**不是** failure(由调用方逐公司缺失计数)。
    return selected


# ── 主客反转归一 ─────────────────────────────────────────────────────────


def _invert_group(group: dict | None, market: str) -> dict | None:
    if group is None:
        return None
    if market == "1x2":
        return {"home": group["away"], "draw": group["draw"], "away": group["home"]}
    if market == "ah":
        return {"home": group["away"], "line": -group["line"], "away": group["home"]}
    # ou 对称,不换
    return dict(group)


def normalize_for_inversion(record: dict, inverted: bool) -> dict:
    """把 provider 视角的记录归一到 canonical(FotMob 主客)视角。

    inverted=True 时:1x2 交换 home/away;AH 交换双边并把盘口线取负;OU 不变。
    始终返回新 dict,不修改入参。
    """
    market = record["market"]
    out = dict(record)
    if not inverted:
        out["initial"] = dict(record["initial"]) if record.get("initial") else None
        out["latest"] = dict(record["latest"]) if record.get("latest") else None
        return out
    out["initial"] = _invert_group(record.get("initial"), market)
    out["latest"] = _invert_group(record.get("latest"), market)
    return out


def canonical_payload_json(record: dict) -> str:
    """排序键的稳定 JSON(hash-diff 的基准表示,配 backend.db.util.sha256_hex 用)。"""
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ── 网络获取(与解析分离;失败抛异常,调用方写 source_health) ─────────────


def _flesh() -> str:
    return str(random.random())


def _http_get(url: str, params: dict) -> str:
    import httpx

    # 2026-08-12 真实回归:直连(本机/生产 launchd 均无代理)时 NowGoal 对
    # SoccerAjax 请求返回 HTTP 200 + 合法 JSON,但 Data 字段静默为空
    # (不是 WAF 拦截页,looks_blocked() 认不出来,过去被误判为"该日期没有
    # 赛程")。THORDATA_PROXY(FotMob 用的英国出口)对 NowGoal 稳定失败
    # (Thordata 服务端 403 "Does not support mainland China servers",
    # HTTPS 场景下客户端库把这个拒绝页误报成 TLS 握手失败)。用独立的
    # THORDATA_PROXY_NOWGOAL(越南出口)则能稳定拿到真实数据——两个 Thordata
    # 配置对同一目标表现不同,具体原因未知,如实按经验值使用。真实抓取一律
    # 要求这个代理,缺失时直接失败,不允许静默退化成"看起来成功但 Data 是空的"
    # 这种更难发现的坏结果(与 backend/providers/nowgoal_archive.py 的
    # NowGoalArchiveTransport 同一纪律)。
    proxy = os.environ.get("THORDATA_PROXY_NOWGOAL")
    if not proxy:
        raise NowGoalError(
            "THORDATA_PROXY_NOWGOAL 未配置——NowGoal 直连、以及 FotMob 用的"
            "THORDATA_PROXY(英国出口)都会静默返回空数据/被拒绝"
            "(2026-08-12 真实验证过),不允许伪装成功。"
        )
    resp = httpx.get(
        url,
        params=params,
        timeout=FETCH_TIMEOUT_SECONDS,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": BASE_URL + "/",
        },
        follow_redirects=True,
        proxy=proxy,
    )
    text = resp.text
    if looks_blocked(text):
        raise WAFBlockedError(f"NowGoal WAF 拦截(HTTP {resp.status_code}): {url}")
    if resp.status_code != 200:
        raise NowGoalError(f"NowGoal HTTP {resp.status_code}: {url}")
    return text


def _extract_data_field(text: str) -> str:
    """响应可能是 JSON({"Data": "..."})或纯文本;统一取 Data 文本。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        data = obj.get("Data")
        if isinstance(data, str):
            return data
        if isinstance(data, list):
            return "\n".join(str(x) for x in data)
    return text


def fetch_schedule(date: str) -> list[dict]:
    """抓取某日日程(date=YYYY-MM-DD)并解析。失败/WAF 抛异常。"""
    text = _http_get(
        SCHEDULE_URL,
        {"type": "6", "date": date, "order": "time", "timezone": "8", "flesh": _flesh()},
    )
    return parse_schedule(_extract_data_field(text))


def fetch_odds(titan_id: str, target_cids=DEFAULT_TARGET_CIDS) -> list[dict]:
    """抓取单场赔率(初盘+最新)并解析为 canonical 记录。失败/WAF 抛异常。"""
    text = _http_get(
        ODDS_URL,
        {"type": "14", "t": "1", "id": str(titan_id), "h": "0", "s": "0", "flesh": _flesh()},
    )
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        raise NowGoalError(f"NowGoal 赔率响应不是 JSON(前 80 字符): {stripped[:80]!r}")
    if not isinstance(payload, dict):
        payload = {"companies": payload}
    return parse_odds(payload, target_cids=target_cids)
