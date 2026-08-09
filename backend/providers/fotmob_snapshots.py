"""FotMob 阵容 / 伤停快照 Provider Adapter。

- 纯提取函数(extract_*)只吃 match_details 的 pageProps dict,离线可测;
- 真实抓取 fetch_match_payload 延迟 import fotmob_client；模块 import 本身不读
  代理配置，默认 live client 构造时才按"已有环境 → 缺失时加载 .env"解析;
- FotMob 不声明阵容/伤停的来源更新时间 → source_updated_at 恒 None,不得编造
  (CLAUDE.md §6.2;SSR 页面另有 5~20 分钟 CDN 缓存,观察时间只能算 observed_at)。
"""


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


def extract_prematch_details(payload: dict, match_id) -> dict:
    """从同一份 match_details payload 定向提取 Referee/Temperature/Wind_Speed 三个
    dim_match 列(裁判/天气赛前数据能力实测,本轮任务)。

    复用 FotMobClient.parse_match_dim 的裁判(3 级 fallback)/天气解析逻辑,不重复
    实现、不产生第二份互相漂移的解析代码。proxy="" 离线构造,不触发任何凭证解析
    (parse_match_dim 本身也不读取任何实例状态,是纯函数式的方法)。

    只返回这 3 个字段——status/kickoff/比分等其余 dim_match 列由
    ingest_future_fixtures.py / ingest_match.py 各自的写路径负责,调用方(narrow
    UPDATE)绝不覆盖。缺失字段如实为 None,不编造(CLAUDE.md §6.2.1)。
    """
    from backend.fotmob_client import FotMobClient

    row = FotMobClient(proxy="").parse_match_dim(payload, match_id=match_id)
    return {
        "Referee": row.get("Referee"),
        "Temperature": row.get("Temperature"),
        "Wind_Speed": row.get("Wind_Speed"),
    }


def fetch_match_payload(match_id):
    """真实抓取 match_details 的 pageProps(经 ThorData 住宅代理)。

    延迟 import 不触发凭证读取；FotMobClient() 只在这个默认 live 构造点解析
    THORDATA_PROXY。已有环境变量时不会读取 .env，缺失时才尝试一次 dotenv，
    最终仍缺失则由 client 抛出不含凭证值的 RuntimeError。
    """
    from backend.fotmob_client import FotMobClient

    client = FotMobClient()
    return client.match_details(match_id)
