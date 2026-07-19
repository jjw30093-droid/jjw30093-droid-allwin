"""FotMob 阵容 / 伤停快照 Provider Adapter。

- 纯提取函数(extract_*)只吃 match_details 的 pageProps dict,离线可测;
- 真实抓取 fetch_match_payload 延迟 import fotmob_client(该模块 import 时
  就要求 THORDATA_PROXY 环境变量,缺失时这里抛清晰异常,不让整个包 import 失败);
- FotMob 不声明阵容/伤停的来源更新时间 → source_updated_at 恒 None,不得编造
  (CLAUDE.md §6.2;SSR 页面另有 5~20 分钟 CDN 缓存,观察时间只能算 observed_at)。
"""

import os


def _player_brief(p: dict) -> dict:
    return {"id": p.get("id"), "name": p.get("name")}


def _sorted_players(players) -> list[dict]:
    out = [_player_brief(p) for p in (players or []) if isinstance(p, dict)]
    return sorted(out, key=lambda x: (str(x["id"]), str(x["name"])))


def extract_lineup_snapshot(match_details_payload: dict) -> dict:
    """从 match_details 的 pageProps 提取阵容最小 canonical 子集。

    结构:{"home"/"away": {team_id, formation, starters[], subs[]}};
    球员列表按 id 排序,保证同一阵容 hash 稳定。
    payload 无 lineup 时给空侧(team_id/formation=None,空列表)——这是一次
    合法观察("尚无阵容"),交给 hash-diff 判断是否变化。
    """
    lineup = (match_details_payload or {}).get("content", {}).get("lineup") or {}
    snapshot = {}
    for out_key, side_key in (("home", "homeTeam"), ("away", "awayTeam")):
        side = lineup.get(side_key) or {}
        snapshot[out_key] = {
            "team_id": side.get("id"),
            "formation": side.get("formation"),
            "starters": _sorted_players(side.get("starters")),
            "subs": _sorted_players(side.get("subs")),
        }
    return snapshot


def _sideline_entry(p: dict) -> dict:
    unavailability = p.get("unavailability") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "reason": unavailability.get("type") or p.get("reason"),
        "expected_return": unavailability.get("expectedReturn") or p.get("expectedReturn"),
    }


def extract_sideline_snapshot(payload: dict, team_id) -> dict:
    """从 match_details 的 pageProps 提取某队伤停名单最小子集。

    来源:content.lineup.{homeTeam,awayTeam}.unavailable。
    返回 {"team_id": ..., "sidelined": [{id, name, reason, expected_return}]}(按 id 排序)。
    该队在 payload 里无 unavailable 时给空列表(合法观察,交给 hash-diff)。
    FotMob 不提供伤停条目的更新时间,这里不输出任何时间字段。
    """
    lineup = (payload or {}).get("content", {}).get("lineup") or {}
    sidelined: list[dict] = []
    for side_key in ("homeTeam", "awayTeam"):
        side = lineup.get(side_key) or {}
        if side.get("id") != team_id:
            continue
        entries = [
            _sideline_entry(p) for p in (side.get("unavailable") or []) if isinstance(p, dict)
        ]
        sidelined = sorted(entries, key=lambda x: (str(x["id"]), str(x["name"])))
        break
    return {"team_id": team_id, "sidelined": sidelined}


def fetch_match_payload(match_id):
    """真实抓取 match_details 的 pageProps(经 ThorData 住宅代理)。

    fotmob_client 在模块 import 时就读 os.environ["THORDATA_PROXY"],
    所以必须延迟 import,并在缺失时抛出可读异常。
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()   # fotmob_client 也依赖 .env,先加载再判断,避免误判缺配置
    except ImportError:
        pass
    if not os.environ.get("THORDATA_PROXY"):
        raise RuntimeError(
            "THORDATA_PROXY 未配置:FotMob 抓取需要 ThorData 住宅代理凭证"
            "(写入 .env,勿硬编码;离线场景请改用已存 payload 调 extract_* 函数)。"
        )
    from backend.fotmob_client import FotMobClient

    client = FotMobClient()
    return client.match_details(match_id)
