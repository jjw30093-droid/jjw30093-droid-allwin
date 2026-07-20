"""小工具:UTC 时间戳、UUID、哈希。全项目统一从这里取,避免各处自造格式。"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """UTC ISO8601,秒级,Z 后缀:2026-07-19T12:00:00Z"""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def new_uuid() -> str:
    return str(uuid.uuid4())


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def new_token(nbytes: int = 32) -> str:
    """≥256bit 随机 token(urlsafe)。数据库只允许存它的 sha256_hex。"""
    return secrets.token_urlsafe(nbytes)


def normalize_utc_iso(value) -> str | None:
    """ISO 时间串 → 'YYYY-MM-DDTHH:MM:SSZ'(UTC)。

    只接受带时间部分的值;纯日期/不可解析 → None(CLAUDE.md §6.2.1:
    来源只给日期时不得补当天 00:00 伪装精确时间)。
    无时区标注时按 UTC 语义处理(FotMob utcTime 即 UTC)。
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if len(s) < 16:
        return None
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    if "T" not in s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── kickoff 精度语义(CLAUDE.md §6.2.1;P0 收口) ──────────────────────
#
# 精确性判定必须依赖显式来源/精度元数据,禁止用"字符串是否含 T"或"是否等于
# 午夜"这类字符串形状推断。exact 的充要条件:
#   1) kickoff_precision 明确为 'exact';
#   2) kickoff_at_utc 能被严格解析、带时区(或按 UTC 规范化后)且落在合法范围;
#   3) 存在非空的 kickoff_source(可追溯来源)。

KICKOFF_PRECISIONS = ("exact", "date_only", "unknown")


def parse_strict_utc(value):
    """低层"按已知数据源语义尽力解析"函数 → tz-aware 的 UTC datetime;不合法返回 None。

    要求:必须是字符串、必须含时间部分(有 'T' 或空格分隔的 HH:MM[:SS])、必须能被
    datetime.fromisoformat 解析。naive datetime(无时区)按 UTC 语义补全后返回——
    **这是有意的宽松行为**,仅用于"这个值能否被当作某个时间点来比较"这类场景
    (如 supersede 的赛前时间比较)。

    ⚠️ 正式的"精确开球时刻"资格判断(是否可 publish/lock、是否 exact)绝不能只调用
    本函数——必须调用 `normalize_exact_kickoff` / `is_exact_kickoff`,它们在本函数
    基础上额外校验显式时区(拒绝 naive)、precision、source 三项,缺一不可。
    纯日期(如 '2025-08-15')、非法格式、非字符串一律 None——不把自然日下界当精确时刻。
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if "T" not in s and " " not in s:
        return None  # 纯日期,无时间部分
    s = s.replace(" ", "T", 1) if "T" not in s else s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _has_explicit_tz(s: str) -> bool:
    """时间串是否带显式时区标注(Z 或 ±HH:MM 偏移)。naive datetime → False。"""
    s = s.strip()
    if s.endswith("Z"):
        return True
    if "T" not in s and " " not in s:
        return False
    time_part = s.replace(" ", "T", 1).split("T", 1)[1]
    return "+" in time_part or "-" in time_part


def normalize_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source) -> str | None:
    """精确开球时刻验证 + 规范化(唯一真源;所有"这是精确开球时间吗"的判断都必须
    经过本函数,不得自行判断字符串是否含 'T'、是否等于午夜,或临时拼造 kickoff_source)。

    四者缺一不可:
      1) kickoff_precision 明确为 'exact';
      2) kickoff_source 非空且非空白(真实来源,不得由调用方臆造替代值);
      3) kickoff_at_utc 带**显式时区**(Z 或 ±HH:MM,拒绝 naive datetime);
      4) 可被严格解析为合法时刻。
    全部满足时返回规范化后的 UTC 'YYYY-MM-DDTHH:MM:SSZ';任何一条不满足返回 None。
    date_only/unknown、缺来源、naive datetime、纯日期、非法格式一律返回 None。
    """
    if kickoff_precision != "exact":
        return None
    if not kickoff_source or not str(kickoff_source).strip():
        return None
    if not isinstance(kickoff_at_utc, str) or not _has_explicit_tz(kickoff_at_utc):
        return None  # naive datetime / 纯日期 / 非字符串 → 不合格
    dt = parse_strict_utc(kickoff_at_utc)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source) -> bool:
    """`normalize_exact_kickoff` 的布尔视图,供只需要是非判断的调用方使用。"""
    return normalize_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source) is not None
