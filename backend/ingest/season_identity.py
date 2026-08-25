"""赛季身份校验的唯一出处(2026-08-25,CLAUDE.md §6.3 事故收口)。

此前"响应的 details.id/selectedSeason 必须与请求一致"这套回声校验在仓库里有
五份互不复用的实现(ingest_future_fixtures / backfill_season_tables /
backfill_kickoff_from_fotmob / active_league_mvp / schedules/fotmob_schedule),
两个模块各自定义了同名的 SeasonIdentityError。本模块收敛为一份:

- `verify_season_echo(data, league_id, season)`:回声校验——caller 指定了
  season,响应必须回显同一个;防 FotMob 在目标赛季尚未发布时静默返回另一个
  赛季的数据被错标落库;
- `discover_season(data, league_id)`:发现模式——不预设赛季,season 由响应
  回报(details.selectedSeason)。这是 FotMob 自己客户端的语义,也是全仓
  唯一自我校验的赛季来源(S1);
- `season_echo_matches(...)`:布尔版,给需要"结构化拒绝原因"而非异常的
  调用方(schedules/fotmob_schedule 的 normalize 校验)用;
- `available_provider_seasons(data)`:提取 `allAvailableSeasons`——来源一直
  下发、此前全仓无人消费的权威赛季清单,正好是
  backend/season_resolver.py::resolve_provider_season 那个从没人传过的参数。

实现主体从 backend/ingest/ingest_future_fixtures.py 原样迁入(那里保留同名
re-export 兼容既有 import);错误类型统一为这里的 SeasonIdentityError。
"""

from __future__ import annotations


class SeasonIdentityError(RuntimeError):
    """league_matches() 响应的 details.id/selectedSeason 与请求参数不一致。

    FotMob 在某个联赛尚未发布下一赛季赛程时,`&season=` 请求参数可能被忽略、
    静默返回当前/上一个已发布赛季的数据。不校验的话,这些行会被当成目标赛季
    写进库里(例如把已完赛的 2025/2026 整季误标成 Season='2026/2027')——
    必须拒绝写库,不能静默降级成"写入了但赛季标错"。
    """


def _observed_league_id(data: dict, context: str) -> int:
    details = data.get("details") or {}
    observed_id = details.get("id")
    try:
        return int(observed_id)
    except (TypeError, ValueError):
        raise SeasonIdentityError(
            f"{context} 响应的 details.id 无法解析为整数: {observed_id!r}"
        ) from None


def verify_season_echo(data: dict, league_id: int, season: str) -> None:
    """请求/响应回声校验:details.id == league_id 且 selectedSeason == season。

    不一致抛 SeasonIdentityError,调用方不得落库。
    """
    context = f"league_matches(league_id={league_id}, season={season!r})"
    observed_id = _observed_league_id(data, context)
    if observed_id != league_id:
        raise SeasonIdentityError(
            f"{context} 响应的 details.id={observed_id} 与请求的 league_id 不一致"
            f"——来源很可能还没有这个赛季的数据,拒绝落库"
        )
    details = data.get("details") or {}
    observed_season = details.get("selectedSeason") or details.get("season")
    # str() 归一:自然年联赛的 selectedSeason 在部分 payload 里是整数 2026,
    # 请求参数是字符串 "2026"——语义相同,不能因类型差异误拒
    # (active_league_mvp 的原实现就带这层 str() 保护,收敛时不能丢)。
    if observed_season is None or str(observed_season) != str(season):
        raise SeasonIdentityError(
            f"{context} 响应的 selectedSeason={observed_season!r} 与请求的 season "
            f"不一致——来源很可能还没有这个赛季的数据,拒绝落库"
        )


def season_echo_matches(data: dict, league_id: int, season: str) -> bool:
    """verify_season_echo 的布尔版(给结构化拒绝原因的校验管线用,不抛异常)。"""
    try:
        verify_season_echo(data, league_id, season)
    except SeasonIdentityError:
        return False
    return True


def discover_season(data: dict, league_id: int) -> str:
    """发现模式:硬断言 details.id == league_id,season 由响应**回报**。

    用于 T+7 赛程同步——不预设赛季串(解决 J1 同时存在 2026 与 2026/2027 的
    换季、以及各联赛跨年/自然年惯例不同)。返回 details.selectedSeason(非空)。
    id 不符或赛季缺失照样抛 SeasonIdentityError,fail-closed。
    """
    context = f"discovery(league_id={league_id})"
    observed_id = _observed_league_id(data, context)
    if observed_id != league_id:
        raise SeasonIdentityError(
            f"{context} 的 details.id={observed_id} 不一致,拒绝落库"
        )
    details = data.get("details") or {}
    season = details.get("selectedSeason") or details.get("season")
    if not season or not str(season).strip():
        raise SeasonIdentityError(f"{context} 响应未回报可用赛季串,拒绝落库")
    return str(season)


def available_provider_seasons(data: dict) -> list[str]:
    """提取来源广告的全部赛季串(details.allAvailableSeasons / 顶层同名键)。

    缺失时返回空列表(如实——旧 payload/低覆盖联赛可能没有),不抛异常;
    调用方按需决定空列表语义(如 season_audit 标 UNVERIFIED)。
    """
    details = data.get("details") or {}
    raw = details.get("allAvailableSeasons") or data.get("allAvailableSeasons") or []
    out = []
    for s in raw:
        if isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out
