"""春秋直播（kbisailive.com）公开足球实时比分适配器。

边界：

- 只使用官网匿名页面公开调用的实时足球 REST 与 WebSocket；
- 不读取 Cookie、账号、验证码、付费内容、``.env`` 或代理凭证；
- REST 返回 ``MatchListResp`` Protobuf，WebSocket 返回 ``MatchMessage``；
- 只解析比赛、赛事、球队、比分和状态字段，主动忽略主播/房间/聊天字段；
- 网络获取、二进制解码和 canonical 归一彼此分离，便于离线测试。

公开前端在 2026-07-31 使用的契约：

- ``POST /api/v1/football/realtimeMatch_b``；
- ``wss://kbisailive.com/ws/match``；
- WebSocket 订阅 type=10，足球比分/状态更新 type=12/13。

这些是对当次公开 Web 客户端的兼容实现，不代表来源承诺稳定 API 或授权商业
再分发。正式上线前仍须由运营方确认来源条款、频率和展示许可。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import httpx

REST_URL = "https://kbisailive.com/api/v1/football/realtimeMatch_b"
WS_URL = "wss://kbisailive.com/ws/match"
PUBLIC_ORIGIN = "https://kbisailive.com"
PROVIDER = "kbisai"

MAX_REST_BYTES = 4 * 1024 * 1024
MAX_PROTO_FIELDS = 250_000
MAX_NESTED_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_STRING_BYTES = 64 * 1024

WS_SUBSCRIBE_FOOTBALL_LIST = 10
WS_FOOTBALL_SCORE_CHANGE = 12
WS_FOOTBALL_STATUS_CHANGE = 13

_STATUS_GROUPS = {
    1: "NOT_STARTED",
    2: "IN_PLAY",
    3: "IN_PLAY",
    4: "IN_PLAY",
    5: "IN_PLAY",
    6: "IN_PLAY",
    7: "IN_PLAY",
    8: "FINISHED",
    9: "OTHER",
    10: "OTHER",
    11: "OTHER",
    12: "OTHER",
    13: "OTHER",
}
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class KbisaiLiveScoreError(Exception):
    """固定、安全的 provider 错误基类。"""


class KbisaiTransportError(KbisaiLiveScoreError):
    """匿名公开 transport 失败。"""


class KbisaiProtocolError(KbisaiLiveScoreError):
    """Protobuf 或来源业务结构不符合已验证契约。"""


@dataclass(frozen=True)
class RealtimeSnapshot:
    """一次匿名 REST 观察结果。"""

    observed_at: str
    raw_bytes: bytes
    raw_sha256: str
    matches: tuple[dict[str, Any], ...]
    competition_count: int
    team_count: int
    live_count: int

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider": PROVIDER,
            "observed_at": self.observed_at,
            "raw_sha256": self.raw_sha256,
            "raw_bytes": len(self.raw_bytes),
            "competition_count": self.competition_count,
            "team_count": self.team_count,
            "match_count": len(self.matches),
            "live_count": self.live_count,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _unix_to_utc_iso(value: int) -> str:
    try:
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise KbisaiProtocolError("invalid match kickoff timestamp") from None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _signed_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


class _ProtoReader:
    """只实现本数据源所需 wire types 的有界 Protobuf reader。"""

    __slots__ = ("_data", "_pos", "_field_count")

    def __init__(self, data: bytes):
        if not isinstance(data, bytes):
            raise KbisaiProtocolError("protobuf payload must be bytes")
        if len(data) > MAX_REST_BYTES:
            raise KbisaiProtocolError("protobuf payload exceeds size limit")
        self._data = data
        self._pos = 0
        self._field_count = 0

    @property
    def done(self) -> bool:
        return self._pos >= len(self._data)

    def read_varint(self) -> int:
        value = 0
        shift = 0
        for _ in range(10):
            if self._pos >= len(self._data):
                raise KbisaiProtocolError("truncated protobuf varint")
            byte = self._data[self._pos]
            self._pos += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
            shift += 7
        raise KbisaiProtocolError("invalid protobuf varint")

    def read_key(self) -> tuple[int, int]:
        self._field_count += 1
        if self._field_count > MAX_PROTO_FIELDS:
            raise KbisaiProtocolError("protobuf field budget exceeded")
        key = self.read_varint()
        field_number, wire_type = key >> 3, key & 0x07
        if field_number <= 0:
            raise KbisaiProtocolError("invalid protobuf field number")
        return field_number, wire_type

    def read_bytes(self, *, limit: int = MAX_NESTED_MESSAGE_BYTES) -> bytes:
        length = self.read_varint()
        if length > limit or self._pos + length > len(self._data):
            raise KbisaiProtocolError("invalid protobuf length-delimited field")
        start = self._pos
        self._pos += length
        return self._data[start : start + length]

    def read_string(self) -> str:
        raw = self.read_bytes(limit=MAX_STRING_BYTES)
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise KbisaiProtocolError("invalid protobuf string") from None

    def skip(self, wire_type: int) -> None:
        if wire_type == 0:
            self.read_varint()
            return
        if wire_type == 1:
            if self._pos + 8 > len(self._data):
                raise KbisaiProtocolError("truncated protobuf fixed64")
            self._pos += 8
            return
        if wire_type == 2:
            self.read_bytes()
            return
        if wire_type == 5:
            if self._pos + 4 > len(self._data):
                raise KbisaiProtocolError("truncated protobuf fixed32")
            self._pos += 4
            return
        raise KbisaiProtocolError("unsupported protobuf wire type")


def _expect_wire(actual: int, expected: int) -> None:
    if actual != expected:
        raise KbisaiProtocolError("unexpected protobuf wire type")


def _parse_packed_int32(data: bytes) -> list[int]:
    reader = _ProtoReader(data)
    values: list[int] = []
    while not reader.done:
        values.append(_signed_int32(reader.read_varint()))
    return values


def _parse_int_item_list(data: bytes) -> list[int]:
    reader = _ProtoReader(data)
    values: list[int] = []
    while not reader.done:
        field, wire = reader.read_key()
        if field == 1 and wire == 2:
            values.extend(_parse_packed_int32(reader.read_bytes()))
        elif field == 1 and wire == 0:
            values.append(_signed_int32(reader.read_varint()))
        else:
            reader.skip(wire)
    return values


def _parse_string_item_list(data: bytes) -> list[str]:
    reader = _ProtoReader(data)
    values: list[str] = []
    while not reader.done:
        field, wire = reader.read_key()
        if field == 1:
            _expect_wire(wire, 2)
            values.append(reader.read_string())
        else:
            reader.skip(wire)
    return values


def _parse_competition(data: bytes) -> dict[str, Any]:
    reader = _ProtoReader(data)
    result: dict[str, Any] = {"id": 0, "name": "", "logo": "", "color": "", "initial": ""}
    names = {2: "name", 3: "logo", 4: "color", 5: "initial"}
    while not reader.done:
        field, wire = reader.read_key()
        if field == 1:
            _expect_wire(wire, 0)
            result["id"] = _signed_int32(reader.read_varint())
        elif field in names:
            _expect_wire(wire, 2)
            result[names[field]] = reader.read_string()
        else:
            reader.skip(wire)
    return result


def _parse_team(data: bytes) -> dict[str, Any]:
    reader = _ProtoReader(data)
    result: dict[str, Any] = {"id": 0, "name": "", "logo": "", "position": None}
    names = {2: "name", 3: "logo", 4: "position"}
    while not reader.done:
        field, wire = reader.read_key()
        if field == 1:
            _expect_wire(wire, 0)
            result["id"] = _signed_int32(reader.read_varint())
        elif field in names:
            _expect_wire(wire, 2)
            result[names[field]] = reader.read_string()
        else:
            reader.skip(wire)
    return result


def _parse_lottery(data: bytes) -> dict[str, Any]:
    reader = _ProtoReader(data)
    result: dict[str, Any] = {"match_id": 0, "lottery_id": 0, "issue_num": ""}
    while not reader.done:
        field, wire = reader.read_key()
        if field in (1, 2):
            _expect_wire(wire, 0)
            result["match_id" if field == 1 else "lottery_id"] = _signed_int32(
                reader.read_varint()
            )
        elif field == 3:
            _expect_wire(wire, 2)
            result["issue_num"] = reader.read_string()
        else:
            reader.skip(wire)
    return result


def _parse_match(data: bytes) -> dict[str, Any]:
    reader = _ProtoReader(data)
    result: dict[str, Any] = {
        "i_arr": [],
        "s_arr": [],
        "home_score": [],
        "away_score": [],
        "room_arr_count": 0,
    }
    while not reader.done:
        field, wire = reader.read_key()
        if field == 1:
            _expect_wire(wire, 2)
            result["i_arr"] = _parse_int_item_list(reader.read_bytes())
        elif field == 2:
            _expect_wire(wire, 2)
            result["s_arr"] = _parse_string_item_list(reader.read_bytes())
        elif field in (3, 4):
            key = "home_score" if field == 3 else "away_score"
            if wire == 2:
                result[key].extend(_parse_packed_int32(reader.read_bytes()))
            elif wire == 0:
                result[key].append(_signed_int32(reader.read_varint()))
            else:
                raise KbisaiProtocolError("unexpected score wire type")
        elif field == 5:
            _expect_wire(wire, 2)
            reader.read_string()
            result["room_arr_count"] += 1
        else:
            # 6/7 是主播/预告数据，不属于实时比分最小数据面。
            reader.skip(wire)
    return result


def decode_match_list_response(data: bytes) -> dict[str, Any]:
    """解码官网公开 REST ``MatchListResp``，不执行网络请求。"""

    reader = _ProtoReader(data)
    result: dict[str, Any] = {
        "competitions": [],
        "teams": [],
        "lotteries": [],
        "matches": [],
        "code": 0,
    }
    parsers = {
        1: ("competitions", _parse_competition),
        2: ("teams", _parse_team),
        3: ("lotteries", _parse_lottery),
        4: ("matches", _parse_match),
    }
    while not reader.done:
        field, wire = reader.read_key()
        if field in parsers:
            _expect_wire(wire, 2)
            key, parser = parsers[field]
            result[key].append(parser(reader.read_bytes()))
        elif field == 5:
            _expect_wire(wire, 0)
            result["code"] = _signed_int32(reader.read_varint())
        else:
            reader.skip(wire)
    if result["code"] != 0:
        raise KbisaiProtocolError("provider returned non-zero protobuf code")
    return result


def _unique_index(rows: Iterable[dict[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, int) or value <= 0:
            raise KbisaiProtocolError(f"invalid {label} identifier")
        if value in indexed:
            raise KbisaiProtocolError(f"duplicate {label} identifier")
        indexed[value] = row
    return indexed


def normalize_match_list(
    decoded: dict[str, Any],
    *,
    observed_at: str,
) -> tuple[dict[str, Any], ...]:
    """把来源压缩数组归一为 all-win 可审计的实时比分记录。"""

    competitions = _unique_index(decoded.get("competitions", []), "id", "competition")
    teams = _unique_index(decoded.get("teams", []), "id", "team")
    lotteries: dict[int, list[dict[str, Any]]] = {}
    for row in decoded.get("lotteries", []):
        match_id = row.get("match_id")
        if isinstance(match_id, int) and match_id > 0:
            lotteries.setdefault(match_id, []).append(row)

    normalized: list[dict[str, Any]] = []
    seen_match_ids: set[int] = set()
    for raw in decoded.get("matches", []):
        ints = raw.get("i_arr")
        strings = raw.get("s_arr")
        if not isinstance(ints, list) or len(ints) < 6:
            raise KbisaiProtocolError("match integer array is incomplete")
        if not isinstance(strings, list):
            raise KbisaiProtocolError("match string array is invalid")

        match_id, competition_id, home_id, away_id, kickoff, status_id = ints[:6]
        if (
            not all(isinstance(value, int) and value > 0 for value in (match_id, competition_id, home_id, away_id))
            or home_id == away_id
        ):
            raise KbisaiProtocolError("match identity is invalid")
        if match_id in seen_match_ids:
            raise KbisaiProtocolError("duplicate match identifier")
        seen_match_ids.add(match_id)
        if competition_id not in competitions or home_id not in teams or away_id not in teams:
            raise KbisaiProtocolError("match references unknown entity")
        if status_id not in _STATUS_GROUPS:
            raise KbisaiProtocolError("unknown football status identifier")

        home_scores = raw.get("home_score")
        away_scores = raw.get("away_score")
        if not isinstance(home_scores, list) or not isinstance(away_scores, list):
            raise KbisaiProtocolError("match score arrays are invalid")
        if len(home_scores) != len(away_scores):
            raise KbisaiProtocolError("match score arrays have different lengths")
        state = _STATUS_GROUPS[status_id]
        home_total = home_scores[0] if home_scores else None
        away_total = away_scores[0] if away_scores else None
        if state == "NOT_STARTED":
            home_total = None
            away_total = None
        elif home_total is None or away_total is None or home_total < 0 or away_total < 0:
            raise KbisaiProtocolError("started match has invalid score")

        competition = competitions[competition_id]
        home = teams[home_id]
        away = teams[away_id]
        normalized.append(
            {
                "provider": PROVIDER,
                "provider_match_id": str(match_id),
                "sport": "football",
                "competition": {
                    "provider_competition_id": str(competition_id),
                    "name": competition["name"],
                },
                "kickoff_at_utc": _unix_to_utc_iso(kickoff),
                "status": state,
                "provider_status_id": status_id,
                # 真实样本显示 iArr[9] 是 epoch-like 时钟参考值而非分钟。
                # 在各 statusId 对应的暂停/半场语义独立验证前不推导比赛分钟。
                "provider_clock_reference": (
                    ints[9] if len(ints) > 9 and ints[9] > 0 else None
                ),
                "clock_semantics": "UNVERIFIED",
                "home_team": {
                    "provider_team_id": str(home_id),
                    "name": home["name"],
                },
                "away_team": {
                    "provider_team_id": str(away_id),
                    "name": away["name"],
                },
                "home_score": home_total,
                "away_score": away_total,
                "home_score_parts": list(home_scores),
                "away_score_parts": list(away_scores),
                "note": strings[2] if len(strings) > 2 and strings[2] else None,
                "lottery_ids": sorted(
                    {
                        row["lottery_id"]
                        for row in lotteries.get(match_id, [])
                        if isinstance(row.get("lottery_id"), int) and row["lottery_id"] > 0
                    }
                ),
                "observed_at": observed_at,
                "source_updated_at": None,
            }
        )

    normalized.sort(key=lambda row: (row["kickoff_at_utc"], int(row["provider_match_id"])))
    return tuple(normalized)


def _request_headers() -> dict[str, str]:
    """复现公开匿名 Web 客户端的非秘密、每请求随机 header。"""

    return {
        "content-type": "application/json",
        "accept": (
            "application/x-protobuf,application/octet-stream,"
            "application/json;q=0.9,*/*;q=0.8"
        ),
        "scope": "web",
        "webID": str(uuid.uuid4()),
        "bk": "w" + str(uuid.uuid4()),
        "timestamp": str(int(time.time() * 1000)),
        "origin": PUBLIC_ORIGIN,
        "referer": PUBLIC_ORIGIN + "/",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
        ),
    }


def fetch_realtime_snapshot(
    *,
    timeout_seconds: float = 20.0,
    observed_at: str | None = None,
    client_factory: Callable[..., Any] = httpx.Client,
) -> RealtimeSnapshot:
    """执行一次匿名实时比分全量快照请求。

    ``trust_env=False`` 保证不会意外读取本机 HTTP(S) 代理环境。错误不会携带
    URL、响应体、header、底层异常文本或异常链。
    """

    transport_failed = False
    response: Any | None = None
    try:
        with client_factory(
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(REST_URL, headers=_request_headers(), content=b"")
    except Exception:
        transport_failed = True
    if transport_failed:
        raise KbisaiTransportError("public live-score request failed") from None
    if response is None or response.status_code != 200:
        raise KbisaiTransportError("public live-score request returned invalid status") from None
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-protobuf":
        raise KbisaiTransportError("public live-score response has invalid content type") from None
    raw = bytes(response.content)
    if not raw or len(raw) > MAX_REST_BYTES:
        raise KbisaiTransportError("public live-score response has invalid size") from None

    observed = observed_at or _utc_now_iso()
    decoded = decode_match_list_response(raw)
    matches = normalize_match_list(decoded, observed_at=observed)
    return RealtimeSnapshot(
        observed_at=observed,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        matches=matches,
        competition_count=len(decoded["competitions"]),
        team_count=len(decoded["teams"]),
        live_count=sum(1 for match in matches if match["status"] == "IN_PLAY"),
    )


def _encode_varint(value: int) -> bytes:
    if value < 0:
        value &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_ws_message_type(message_type: int) -> bytes:
    """编码只含 ``MatchMessage.type`` 的公开订阅/心跳消息。"""

    if not isinstance(message_type, int) or message_type <= 0:
        raise KbisaiProtocolError("invalid websocket message type")
    return _encode_varint((3 << 3) | 0) + _encode_varint(message_type)


def build_ws_url(*, epoch_seconds: int | None = None) -> str:
    """复现公开前端的 MD5+Base64 匿名握手参数；它不是账号凭证。"""

    timestamp = int(time.time()) if epoch_seconds is None else epoch_seconds
    if timestamp <= 0:
        raise KbisaiProtocolError("invalid websocket timestamp")
    payload = {
        "scope": "web",
        "timestamp": timestamp,
        "sign": hashlib.md5(f"web{timestamp}".encode("ascii")).hexdigest(),
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return WS_URL + "?" + urlencode({"sign": encoded})


def _parse_score_obj(data: bytes) -> dict[str, Any]:
    reader = _ProtoReader(data)
    result: dict[str, Any] = {
        "match_id": 0,
        "status_id": 0,
        "home_score": [],
        "away_score": [],
        "going_time": 0,
        "note": "",
    }
    while not reader.done:
        field, wire = reader.read_key()
        if field in (1, 2, 5):
            _expect_wire(wire, 0)
            name = {1: "match_id", 2: "status_id", 5: "going_time"}[field]
            result[name] = _signed_int32(reader.read_varint())
        elif field in (3, 4):
            name = "home_score" if field == 3 else "away_score"
            if wire == 2:
                result[name].extend(_parse_packed_int32(reader.read_bytes()))
            elif wire == 0:
                result[name].append(_signed_int32(reader.read_varint()))
            else:
                raise KbisaiProtocolError("unexpected websocket score wire type")
        elif field == 6:
            _expect_wire(wire, 2)
            result["note"] = reader.read_string()
        else:
            reader.skip(wire)
    return result


def decode_ws_message(data: bytes) -> dict[str, Any]:
    """解码 WebSocket ``MatchMessage`` 的比分/状态最小字段集。"""

    reader = _ProtoReader(data)
    result: dict[str, Any] = {
        "match_id": None,
        "timestamp": None,
        "type": 0,
        "score": None,
    }
    while not reader.done:
        field, wire = reader.read_key()
        if field in (1, 2, 3):
            _expect_wire(wire, 0)
            value = _signed_int32(reader.read_varint())
            result[{1: "match_id", 2: "timestamp", 3: "type"}[field]] = value
        elif field == 4:
            _expect_wire(wire, 2)
            result["score"] = _parse_score_obj(reader.read_bytes())
        else:
            # 统计、文字直播、房间、主播及封禁数据不进入本 Provider。
            reader.skip(wire)
    return result


def normalize_ws_score_update(message: dict[str, Any], *, observed_at: str) -> dict[str, Any] | None:
    """只接收 type=12/13 的足球比分或状态变更。"""

    if message.get("type") not in (WS_FOOTBALL_SCORE_CHANGE, WS_FOOTBALL_STATUS_CHANGE):
        return None
    score = message.get("score")
    if not isinstance(score, dict):
        raise KbisaiProtocolError("websocket score update has no score payload")
    match_id = score.get("match_id") or message.get("match_id")
    status_id = score.get("status_id")
    home = score.get("home_score")
    away = score.get("away_score")
    if (
        not isinstance(match_id, int)
        or match_id <= 0
        or status_id not in _STATUS_GROUPS
        or not isinstance(home, list)
        or not isinstance(away, list)
        or len(home) != len(away)
    ):
        raise KbisaiProtocolError("websocket score update is invalid")
    state = _STATUS_GROUPS[status_id]
    home_total = home[0] if home else None
    away_total = away[0] if away else None
    if state != "NOT_STARTED" and (
        home_total is None or away_total is None or home_total < 0 or away_total < 0
    ):
        raise KbisaiProtocolError("websocket started score is invalid")
    if state == "NOT_STARTED":
        home_total = away_total = None
    return {
        "provider": PROVIDER,
        "provider_match_id": str(match_id),
        "event_type": (
            "SCORE_CHANGE"
            if message["type"] == WS_FOOTBALL_SCORE_CHANGE
            else "STATUS_CHANGE"
        ),
        "status": state,
        "provider_status_id": status_id,
        "provider_clock_reference": score.get("going_time") or None,
        "clock_semantics": "UNVERIFIED",
        "home_score": home_total,
        "away_score": away_total,
        "home_score_parts": list(home),
        "away_score_parts": list(away),
        "note": score.get("note") or None,
        "provider_event_timestamp": message.get("timestamp"),
        "observed_at": observed_at,
        "source_updated_at": None,
    }


def listen_ws_updates(
    *,
    duration_seconds: float = 15.0,
    max_events: int = 20,
    session_factory: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """短连接读取公开 WebSocket 的比分/状态增量。

    这是显式调用的有限观察，不自动重连、不无限守护。超过 55 秒或 100 条会被
    拒绝，避免一次 CLI 调用变成无界采集器。
    """

    if not 0.1 <= duration_seconds <= 55.0 or not 1 <= max_events <= 100:
        raise KbisaiProtocolError("websocket observation limits are invalid")
    if session_factory is None:
        from curl_cffi.requests import Session

        session_factory = Session

    event_queue: queue.Queue[bytes | BaseException | None] = queue.Queue()
    ws_holder: list[Any] = []

    def worker() -> None:
        failed: BaseException | None = None
        session = None
        ws = None
        try:
            session = session_factory(timeout=max(5.0, duration_seconds + 3.0))
            # curl_cffi 会自行生成标准 WebSocket 握手头。额外注入 Origin 会在
            # 部分版本形成重复 header，来源返回握手失败；公开签名 URL 已足够。
            ws = session.ws_connect(build_ws_url())
            ws_holder.append(ws)
            ws.send(encode_ws_message_type(WS_SUBSCRIBE_FOOTBALL_LIST))
            while True:
                payload, _flags = ws.recv()
                event_queue.put(bytes(payload))
        except BaseException as exc:  # worker 必须把异常传回主线程并结束。
            failed = exc
        finally:
            if failed is not None:
                event_queue.put(failed)
            event_queue.put(None)
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    thread = threading.Thread(target=worker, name="kbisai-ws", daemon=True)
    thread.start()
    deadline = time.monotonic() + duration_seconds
    updates: list[dict[str, Any]] = []
    transport_failed = False
    while len(updates) < max_events:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            item = event_queue.get(timeout=remaining)
        except queue.Empty:
            break
        if item is None:
            break
        if isinstance(item, BaseException):
            transport_failed = True
            break
        decoded = decode_ws_message(item)
        normalized = normalize_ws_score_update(decoded, observed_at=_utc_now_iso())
        if normalized is not None:
            updates.append(normalized)

    for ws in ws_holder:
        try:
            ws.terminate()
        except Exception:
            pass
    thread.join(timeout=2.0)
    if transport_failed and not updates:
        raise KbisaiTransportError("public live-score websocket failed") from None
    return tuple(updates)


def safe_exception_class(exc: BaseException) -> str:
    """仅供审计摘要使用；绝不返回异常原文。"""

    name = type(exc).__name__
    return name if _SAFE_IDENTIFIER.fullmatch(name) else "ExternalError"


def assert_no_runtime_credentials() -> None:
    """静态运行边界：本 Provider 不需要任何 secret 环境变量。

    函数仅供 CLI 显式表达契约；不会读取或打印环境值。
    """

    forbidden_names = (
        "KBISAI_TOKEN",
        "KBISAI_COOKIE",
        "KBISAI_USERNAME",
        "KBISAI_PASSWORD",
    )
    if any(name in os.environ for name in forbidden_names):
        raise KbisaiTransportError("credentialed kbisai mode is not supported")
