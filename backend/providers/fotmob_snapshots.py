"""FotMob 阵容 / 伤停快照 Provider Adapter。

- 纯提取函数(extract_*)只吃 match_details 的 pageProps dict,离线可测;
- 真实抓取 fetch_match_payload 延迟 import fotmob_client；模块 import 本身不读
  代理配置，默认 live client 构造时才按"已有环境 → 缺失时加载 .env"解析;
- FotMob 不声明阵容/伤停的来源更新时间 → source_updated_at 恒 None,不得编造
  (CLAUDE.md §6.2;SSR 页面另有 5~20 分钟 CDN 缓存,观察时间只能算 observed_at)。
"""


def _layout_coord(v) -> float | None:
    """从 verticalLayout.x / verticalLayout.y 取一个数字坐标,非数字/缺失如实
    None——0 是合法坐标值(门将常见 y≈0.1,不是 0),不能当哨兵用 `or` 短路。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return None


def _player_brief(p: dict) -> dict:
    layout = p.get("verticalLayout")
    layout = layout if isinstance(layout, dict) else {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "shirt_number": p.get("shirtNumber"),
        "pos_x": _layout_coord(layout.get("x")),
        "pos_y": _layout_coord(layout.get("y")),
    }


def _sorted_players(players) -> list[dict]:
    out = [_player_brief(p) for p in (players or []) if isinstance(p, dict)]
    return sorted(out, key=lambda x: (str(x["id"]), str(x["name"])))


def _coach_brief(c) -> dict | None:
    """只取 {id, name}。刻意丢弃 age / primaryTeamId / primaryTeamName /
    countryCode / firstName / lastName / isCoach / usualPlayingPositionId:
    age 每年生日 +1、primaryTeam* 赛季中可变,把它们纳入 canonical payload 等于
    给每场比赛凭空制造一次假的"阵容变化"。没有可显示姓名时如实 None,不返回
    {} 也不返回空串(CLAUDE.md §6.2 缺失即 NULL,不猜测)。coach 是 dict,
    canonical_payload_json 的 sort_keys=True 已递归归一,不需要像
    _sorted_players 那样再排序(那是因为数组有序)。"""
    if not isinstance(c, dict):
        return None
    name = c.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return {"id": c.get("id"), "name": name}


def extract_lineup_snapshot(match_details_payload: dict) -> dict:
    """从 match_details 的 pageProps 提取阵容最小 canonical 子集。

    结构:{"lineup_type", "source", "home"/"away": {team_id, formation, coach,
    starters[], subs[]}};球员列表按 id 排序,保证同一阵容 hash 稳定。
    payload 无 lineup 时给空侧(team_id/formation/coach=None,空列表)——这是
    一次合法观察("尚无阵容"),交给 hash-diff 判断是否变化。

    lineup_type/source(2026-08-15 新增,真实探测:content.lineup 顶层字段,
    homeTeam/awayTeam 的兄弟节点,不是每队各自一份):真实观测值
    lineup_type="lastStarting11"(上一场首发,不是本场确认阵容)、
    source="lastStartingLineups"。展示层必须用这个字段区分"预计 vs 已确认",
    不得把它当成本场官方名单(CLAUDE.md §6.2 不伪装精确度)。旧快照(本字段
    上线前写入的行)没有这两个键,读侧按缺失处理,不回填猜测值。

    coach(2026-08-18 新增,Fix 2):来源路径 content.lineup.{home,away}Team.coach
    ——每侧一个,两队教练不同,绝不提到顶层(与 lineup_type 提到顶层的理由正好
    相反,那个字段一场只有一份)。只取 {id, name},见 _coach_brief 的字段取舍
    理由。旧快照没有这个键,读侧按缺失处理(None),不回填猜测值。

    pos_x/pos_y(2026-08-19 新增,Fix H8):修复真实用户报告的门将站位错误(马竞
    vs 马拉加,奥布拉克被画成中卫)。根因是 _sorted_players 为了 hash 稳定按
    id 重排首发数组,销毁了来源数组"门将在前、沿球场纵深排列"的真实顺序,
    前端 rowsFor 又天真地把重排后的 starters[0] 当门将——两个问题叠加导致
    整张球场图的分行都不可信,不只是门将。仓内 fixture(prematch-5104961.json)
    实测确认上游随每名球员下发 verticalLayout.{x,y}(归一化 0..1 坐标,y 越小
    离己方球门越近),取这两个数字随 id 一起存,前端据此重新按真实坐标分行,
    不再依赖数组顺序,和"按 id 排序保证 hash 稳定"这条既有不变量完全不冲突。
    只取 x/y 两个数字,不取 positionId/usualPlayingPositionId——这两个字段的
    完整取值含义没有逐值验证过,不能拿未经验证的枚举去决定"谁是门将"这种会被
    用户看到并较真的展示细节。旧快照没有 verticalLayout 键,如实 None,不回填
    猜测坐标(前端据此退化成不画球场图,而不是继续按错误顺序画错位置)。
    """
    lineup = (match_details_payload or {}).get("content", {}).get("lineup") or {}
    snapshot = {
        "lineup_type": lineup.get("lineupType"),
        "source": lineup.get("source"),
    }
    for out_key, side_key in (("home", "homeTeam"), ("away", "awayTeam")):
        side = lineup.get(side_key) or {}
        snapshot[out_key] = {
            "team_id": side.get("id"),
            "formation": side.get("formation"),
            "coach": _coach_brief(side.get("coach")),
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
