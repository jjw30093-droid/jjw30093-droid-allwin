"""春秋直播（kbisailive.com）赛程发现 + 赔率变化序列适配器。

与 backend/providers/kbisai_live_scores.py（实时比分）是同一个站点的两组不同
接口，共用同一份边界纪律：

- 只使用官网匿名页面公开调用的接口，不读取 Cookie、账号、验证码、付费内容、
  ``.env`` 或代理凭证；
- ``futureMatch_b``（赛程发现）复用 kbisai_live_scores.py 已经验证过的
  ``MatchListResp`` Protobuf 解码器（同一份公开响应形状，实测 2026-08-07:
  132 个赛事、905 支球队、453 场比赛，正常解码）；
- ``comp/category`` / ``matchAllOdds`` / ``allCompany`` 三个接口返回
  ``{"code":0,"msg":"...","data":"<base64 AES-256-CBC ZeroPadding 密文>"}``,
  解密后是普通 JSON；
- 网络获取、解密与 canonical 归一彼此分离，便于离线测试;
- 这是对当次公开 Web 客户端的兼容实现，不代表来源承诺稳定 API 或授权商业
  再分发。正式上线前仍须由运营方确认来源条款、频率和展示许可。

AES 密钥/IV 来源与性质：
    这不是账号凭证——它们是 kbisailive.com 自己公开发布、随每次页面加载
    发送给所有匿名访客浏览器的静态常量，写死在站点自己的公开 JS bundle 里
    (2026-08-04 抓取自 https://kbisailive.com/assets/index-CrHhdrBu.js)，
    任何人打开浏览器 devtools 都能看到。因此:
    - 作为模块级常量硬编码,不放进 .env(那是给真正的账号/API 密钥用的);
    - assert_no_runtime_credentials() 的语义不受影响——本模块依然不需要、
      不读取任何运行期 secret 环境变量。
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from backend.db.util import parse_strict_utc, sha256_hex
from backend.providers.kbisai_live_scores import (
    MAX_REST_BYTES,
    PROVIDER,
    PUBLIC_ORIGIN,
    KbisaiProtocolError,
    KbisaiTransportError,
    _STATUS_GROUPS,
    _request_headers,
    _unix_to_utc_iso,
    decode_match_list_response,
    normalize_match_list,
)

__all__ = [
    "PROVIDER",
    "ODDS_TYPES",
    "CANONICAL_MARKET_BY_ODDS_TYPE",
    "KbisaiOddsError",
    "fetch_competition_category",
    "fetch_all_companies",
    "fetch_future_matches",
    "fetch_match_all_odds",
    "parse_match_all_odds_points",
]

FOOTBALL_BASE = "https://kbisailive.com/api/v1/football"
COMMON_BASE = "https://kbisailive.com/api/v1/common"
COMP_CATEGORY_URL = f"{FOOTBALL_BASE}/comp/category"
FUTURE_MATCH_URL = f"{FOOTBALL_BASE}/futureMatch_b"
MATCH_ALL_ODDS_URL = f"{FOOTBALL_BASE}/matchAllOdds"
ALL_COMPANY_URL = f"{COMMON_BASE}/nm/allCompany"

# 站点自己公开 JS bundle 里的静态常量,见模块 docstring。
_AES_KEY = base64.b64decode("EZ5F6osgMT8cvN7i/9w1BvJAOMWJwmwLlRmwsw25dFQ=")
_AES_IV = base64.b64decode("MvGFLXPgUNZUoeFfhCC+vw==")

# 来源市场类型 → 仓库 canonical 市场词表(CLAUDE.md §6.3 market CHECK 词表)。
ODDS_TYPES = ("eu", "asia", "bs")
CANONICAL_MARKET_BY_ODDS_TYPE = {"eu": "1x2", "asia": "ah", "bs": "ou"}

_MATCH_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class KbisaiOddsError(KbisaiProtocolError):
    """comp/category、matchAllOdds、allCompany 响应结构不符合已验证契约。"""


def _aes_decrypt_zero_padded(ciphertext: bytes) -> bytes:
    """AES-256-CBC 解密 + 去 ZeroPadding(不是 PKCS7——来源用零字节右填充,
    这里直接 rstrip(b"\\x00")）。纯函数,不发起网络请求。"""
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise KbisaiOddsError("ciphertext has invalid block size")
    try:
        cipher = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV))
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    except Exception:
        raise KbisaiOddsError("aes decrypt failed") from None
    return plaintext.rstrip(b"\x00")


def _decode_encrypted_envelope(response_bytes: bytes) -> Any:
    """``{"code":0,"msg":"...","data":"<base64 密文>"}`` → 解密后的 JSON 值。

    code!=0、data 缺失/不是合法 base64、解密后不是合法 JSON 都判定为协议错误,
    不猜测、不返回部分结果。"""
    if not response_bytes or len(response_bytes) > MAX_REST_BYTES:
        raise KbisaiOddsError("encrypted envelope has invalid size")
    try:
        envelope = json.loads(response_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise KbisaiOddsError("response is not valid json envelope") from None
    if not isinstance(envelope, dict):
        raise KbisaiOddsError("response envelope is not a json object")
    if envelope.get("code") != 0:
        raise KbisaiOddsError("provider returned non-zero envelope code")
    data_b64 = envelope.get("data")
    if not isinstance(data_b64, str) or not data_b64:
        raise KbisaiOddsError("envelope missing data field")
    try:
        ciphertext = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise KbisaiOddsError("envelope data is not valid base64") from None
    plaintext = _aes_decrypt_zero_padded(ciphertext)
    try:
        return json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise KbisaiOddsError("decrypted payload is not valid json") from None


def _post_json(
    url: str,
    body: dict,
    *,
    timeout_seconds: float,
    client_factory: Callable[..., Any],
) -> bytes:
    """POST 一个 JSON 请求体,返回原始响应字节。所有 transport 失败统一转成
    KbisaiTransportError,不携带 URL/响应体/底层异常文本或异常链(与
    kbisai_live_scores.fetch_realtime_snapshot 同款 flag-then-raise-outside-except
    模式,避免 raise ... from <exc> 让底层异常文本经 __context__ 泄漏)。"""
    transport_failed = False
    response: Any | None = None
    try:
        with client_factory(
            timeout=timeout_seconds, trust_env=False, follow_redirects=False
        ) as client:
            response = client.post(
                url, headers=_request_headers(), content=json.dumps(body).encode("utf-8")
            )
    except Exception:
        transport_failed = True
    if transport_failed:
        raise KbisaiTransportError("public kbisai request failed") from None
    if response is None or response.status_code != 200:
        raise KbisaiTransportError("public kbisai request returned invalid status") from None
    return bytes(response.content)


def _get(
    url: str,
    *,
    timeout_seconds: float,
    client_factory: Callable[..., Any],
) -> bytes:
    """GET 请求(allCompany 只接受 GET;POST 会返回 code 9999 服务器未知错误,
    2026-08-04 实测确认)。"""
    transport_failed = False
    response: Any | None = None
    try:
        with client_factory(
            timeout=timeout_seconds, trust_env=False, follow_redirects=False
        ) as client:
            response = client.get(url, headers=_request_headers())
    except Exception:
        transport_failed = True
    if transport_failed:
        raise KbisaiTransportError("public kbisai request failed") from None
    if response is None or response.status_code != 200:
        raise KbisaiTransportError("public kbisai request returned invalid status") from None
    return bytes(response.content)


def fetch_competition_category(
    *,
    timeout_seconds: float = 20.0,
    client_factory: Callable[..., Any] = httpx.Client,
) -> dict[str, Any]:
    """联赛分类树(用于发现某个联赛的 kbisai competitionId)。

    返回解密后的原始树形结构 {"categorys": [...], "tops": [...]}——查找具体
    联赛 id 是调用方的职责,这里不做任何"猜测最相似的名字"之类的模糊匹配。
    """
    raw = _post_json(
        COMP_CATEGORY_URL, {"sportType": "football"},
        timeout_seconds=timeout_seconds, client_factory=client_factory,
    )
    result = _decode_encrypted_envelope(raw)
    # 只检查"是个 dict"太弱——allCompany 的响应解密后也是个 dict,会被这条
    # 校验静默放过。显式要求 categorys 键存在,防止跟别的 kbisai 端点的响应
    # 混在一起也检测不出来。
    if not isinstance(result, dict) or "categorys" not in result:
        raise KbisaiOddsError("comp/category payload is missing 'categorys'")
    return result


def fetch_all_companies(
    *,
    timeout_seconds: float = 20.0,
    client_factory: Callable[..., Any] = httpx.Client,
) -> dict[str, str]:
    """公司 id → 展示名映射。接口不支持按 id 过滤,一次拿全量(~15 家)。"""
    raw = _get(
        ALL_COMPANY_URL, timeout_seconds=timeout_seconds, client_factory=client_factory
    )
    result = _decode_encrypted_envelope(raw)
    if not isinstance(result, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in result.items()
    ):
        raise KbisaiOddsError("allCompany payload is not a flat string map")
    return result


def fetch_future_matches(
    match_date: str,
    *,
    observed_at: str | None = None,
    timeout_seconds: float = 20.0,
    client_factory: Callable[..., Any] = httpx.Client,
) -> tuple[dict[str, Any], ...]:
    """某一天(``matchDate``,当天 24 小时窗口,来源自己的分桶规则未验证到底
    以什么时区切分)的全量赛程。复用 kbisai_live_scores 的 Protobuf 解码器——
    这是同一个 ``MatchListResp`` 响应形状,实测确认过(2026-08-07:132 个赛事、
    905 支球队、453 场比赛,正常解码)。

    只做发现,不做任何"这场是不是我要的联赛"的过滤——调用方按
    competition.provider_competition_id 自己筛选。
    """
    if not _MATCH_DATE_RE.match(match_date):
        raise KbisaiOddsError("match_date must be YYYY-MM-DD")
    transport_failed = False
    response: Any | None = None
    try:
        with client_factory(
            timeout=timeout_seconds, trust_env=False, follow_redirects=False
        ) as client:
            response = client.post(
                FUTURE_MATCH_URL, headers=_request_headers(),
                content=json.dumps({"matchDate": match_date}).encode("utf-8"),
            )
    except Exception:
        transport_failed = True
    if transport_failed:
        raise KbisaiTransportError("public kbisai request failed") from None
    if response is None or response.status_code != 200:
        raise KbisaiTransportError("public kbisai request returned invalid status") from None
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-protobuf":
        raise KbisaiTransportError("futureMatch_b response has invalid content type") from None
    raw = bytes(response.content)
    if not raw or len(raw) > MAX_REST_BYTES:
        raise KbisaiTransportError("futureMatch_b response has invalid size") from None
    decoded = decode_match_list_response(raw)
    return normalize_match_list(decoded, observed_at=observed_at or _utc_now_iso())


def fetch_match_all_odds(
    match_id: int,
    odds_type: str,
    *,
    timeout_seconds: float = 20.0,
    client_factory: Callable[..., Any] = httpx.Client,
) -> dict[str, Any]:
    """某场比赛某个市场(eu/asia/bs)全部公司的完整变化序列(从初盘到现在)。

    返回解密后的原始结构 {"<companyId>": {"statusMatchOdds": [...]}, ...}
    (来源按 changeTime 倒序,新到旧)——company 过滤、逐点 canonical 归一
    (含 ah/ou 盘口线提取)是调用方的职责,不在这个纯网络获取函数里做。
    """
    if odds_type not in ODDS_TYPES:
        raise KbisaiOddsError(f"odds_type must be one of {ODDS_TYPES}")
    if not isinstance(match_id, int) or match_id <= 0:
        raise KbisaiOddsError("match_id must be a positive integer")
    raw = _post_json(
        MATCH_ALL_ODDS_URL, {"matchId": match_id, "oddsType": odds_type},
        timeout_seconds=timeout_seconds, client_factory=client_factory,
    )
    result = _decode_encrypted_envelope(raw)
    # 空字典是合法结果(该场比赛这个市场暂无任何公司数据);非空时每个 value
    # 必须有 statusMatchOdds 列表——只检查"是个 dict"太弱,会放过形状完全不同
    # 的响应(比如误把 allCompany 的 {id: name} 当成 matchAllOdds 解析)。
    if not isinstance(result, dict) or not all(
        isinstance(v, dict) and isinstance(v.get("statusMatchOdds"), list)
        for v in result.values()
    ):
        raise KbisaiOddsError("matchAllOdds payload has unexpected shape")
    return result


def derive_market_phase(
    source_status_id: Any,
    source_updated_at: str | None,
    kickoff_at_utc: str | None,
) -> str:
    """复用 kbisai_live_scores._STATUS_GROUPS(1=NOT_STARTED, 2-7=IN_PLAY,
    8=FINISHED, 9-13=OTHER)。

    ⚠️ 该枚举是在 protobuf 比分端点(realtimeMatch_b/futureMatch_b)上验证的,
    跨到 AES-JSON 的 matchAllOdds 端点复用 **未经独立验证**——这是目前唯一
    可用的信号,不是已确认的事实,调用方与文档都必须如实标注 UNVERIFIED。

    规则(CLAUDE.md §6.3):
      - pre_match:NOT_STARTED 且 source_updated_at 严格早于 kickoff_at_utc
        (两者都必须能解析,否则不满足);
      - in_play:IN_PLAY 或 FINISHED;
      - 其余(未枚举的 statusId、OTHER、缺 kickoff、缺 source_updated_at、
        NOT_STARTED 但观测时间不早于 kickoff)一律 'unknown'——不猜测。
    """
    group = _STATUS_GROUPS.get(source_status_id)
    if group in ("IN_PLAY", "FINISHED"):
        return "in_play"
    if group == "NOT_STARTED" and source_updated_at and kickoff_at_utc:
        observed_dt = parse_strict_utc(source_updated_at)
        kickoff_dt = parse_strict_utc(kickoff_at_utc)
        if observed_dt is not None and kickoff_dt is not None and observed_dt < kickoff_dt:
            return "pre_match"
    return "unknown"


def parse_match_all_odds_points(
    raw_response: dict[str, Any],
    source_market: str,
    *,
    provider_match_id: str,
    fotmob_match_id: int | None,
    kickoff_at_utc: str | None,
    observed_at: str,
    ingested_at: str,
    poll_run_id: str | None,
    target_company_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """把 fetch_match_all_odds() 的原始响应展开成 bronze_kbisai_odds_point 的行。

    纯函数,不发网络请求、不碰数据库。市场映射:eu→1x2(oddsInfo=
    [home,draw,away,closed]),asia→ah / bs→ou(oddsInfo=
    [home_or_over_water, 盘口线, away_or_under_water, closed])——盘口线固定
    在下标 1,这是 ah/ou 两个市场唯一带 handicap_line 的字段来源。

    结构不符合已验证契约的条目(oddsInfo 缺失/长度不足)直接跳过,不编造
    缺省值、不让整批因为一条脏数据而失败——调用方(CLI)可以用
    "来源条目数 vs 返回行数"来发现被跳过的数量并如实汇报,这个函数本身
    不负责统计/汇报。

    company_name 留空(""),由调用方(有 fetch_all_companies() 的结果时)决定
    是否回填——这里不做任何网络请求,不该也不能替调用方做这个决定。
    """
    if source_market not in ODDS_TYPES:
        raise KbisaiOddsError(f"source_market must be one of {ODDS_TYPES}")
    canonical_market = CANONICAL_MARKET_BY_ODDS_TYPE[source_market]

    rows: list[dict[str, Any]] = []
    # dup_ordinal:按(company_id, source_updated_at, point_hash)分组,组内按
    # 来源给出的原始顺序递增编号——保证同一批次里字节完全相同的重复条目也能
    # 各自落一行,而不是在写入层被 UNIQUE 悄悄当成"重复"拒收。
    ordinal_counters: dict[tuple[str, str | None, str], int] = {}

    for company_id, company_data in raw_response.items():
        if target_company_ids is not None and str(company_id) not in target_company_ids:
            continue
        if not isinstance(company_data, dict):
            continue
        points = company_data.get("statusMatchOdds")
        if not isinstance(points, list):
            continue
        for entry in points:
            if not isinstance(entry, dict):
                continue
            odds_info = entry.get("oddsInfo")
            if not isinstance(odds_info, list) or len(odds_info) < 4:
                continue

            change_time = entry.get("changeTime")
            source_updated_at = None
            if isinstance(change_time, int):
                try:
                    source_updated_at = _unix_to_utc_iso(change_time)
                except KbisaiProtocolError:
                    source_updated_at = None

            status_id = entry.get("statusId") if isinstance(entry.get("statusId"), int) else None
            market_phase = derive_market_phase(status_id, source_updated_at, kickoff_at_utc)

            slot_a, slot_b, slot_c, slot_closed = odds_info[0], odds_info[1], odds_info[2], odds_info[3]
            if canonical_market == "1x2":
                handicap_line = None
                odds_home_or_over, odds_draw, odds_away_or_under = slot_a, slot_b, slot_c
            else:
                handicap_line = slot_b
                odds_draw = None
                odds_home_or_over, odds_away_or_under = slot_a, slot_c

            closed_flag = None
            try:
                closed_flag = 1 if int(slot_closed) else 0
            except (TypeError, ValueError):
                closed_flag = None

            raw_point_json = json.dumps(
                entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            point_hash = sha256_hex(raw_point_json)

            group_key = (str(company_id), source_updated_at, point_hash)
            dup_ordinal = ordinal_counters.get(group_key, 0)
            ordinal_counters[group_key] = dup_ordinal + 1

            rows.append({
                "provider": PROVIDER,
                "provider_match_id": provider_match_id,
                "fotmob_match_id": fotmob_match_id,
                "market": canonical_market,
                "source_market": source_market,
                "company_id": str(company_id),
                "company_name": "",
                "handicap_line": handicap_line,
                "odds_home_or_over": odds_home_or_over,
                "odds_draw": odds_draw,
                "odds_away_or_under": odds_away_or_under,
                "closed_flag": closed_flag,
                "source_status_id": status_id,
                "going_time": entry.get("goingTime") if isinstance(entry.get("goingTime"), str) else None,
                "score": entry.get("score") if isinstance(entry.get("score"), str) else None,
                "market_phase": market_phase,
                "source_updated_at": source_updated_at,
                "observed_at": observed_at,
                "ingested_at": ingested_at,
                "poll_run_id": poll_run_id,
                "raw_point_json": raw_point_json,
                "point_hash": point_hash,
                "dup_ordinal": dup_ordinal,
            })
    return rows


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
