/**
 * 比赛信息卡:球场(含容纳人数/场地表面/地图链接)、天气(2026-08-20 初版;
 * 2026-08-24 对齐 FotMob 场地天气卡,补容纳人数/草皮/经纬度,主裁行移出到
 * 独立的 RefereeCard——FotMob 把"场地天气"和"裁判"做成相邻两张卡,不混)。
 *
 * 数据来自 FotMob 赛前抓取(backend/fotmob_client.py::parse_match_dim,
 * migrations/core/0010);各行独立判空,球场/天气全空时整卡不渲染。
 * 天气中文优先按 weather_localized_key 查 WEATHER_CONDITION_ZH(FotMob 官方
 * 枚举 key + 官方简中对照,比关键词正则可靠);查不到再退回 description
 * 关键词匹配;仍命不中如实展示英文原文,不猜译文(CLAUDE.md §2.2)。
 */

import type { MatchDetailResponse } from "@/lib/api-v1";
import { VENUE_SURFACE_ZH, WEATHER_CONDITION_ZH } from "@/components/matches/zh";
import styles from "./MatchInfoCard.module.css";

type Match = MatchDetailResponse["match"];

/** 天气描述/枚举 key → 图标,关键词命中即返回;顺序即优先级:一段描述常常
 * 同时提到风和云("Partly Cloudy/Wind"),排在前面的关键词先命中。同一张表
 * 同时服务 description("Partly Cloudy")与 localizedKey
 * ("weather_condition_partly_cloudy")——两种字符串都含同样的英文词干。 */
const WEATHER_KEYWORDS: Array<[RegExp, string, string]> = [
  [/thunder|storm/i, "⛈", "雷雨"],
  [/snow/i, "🌨", "雪"],
  [/rain|shower|drizzle/i, "🌧", "雨"],
  [/fog|mist|haze/i, "🌫", "雾"],
  [/wind/i, "💨", "大风"],
  [/cloud|overcast/i, "⛅", "多云"],
  [/clear|sunny/i, "☀", "晴"],
];

function weatherIconFor(source: string): string {
  const hit = WEATHER_KEYWORDS.find(([re]) => re.test(source));
  return hit ? hit[1] : "🌤";
}

/** 天气类别中文:localizedKey 官方对照优先 → description 关键词 → 英文原文。 */
function weatherText(match: Match): { icon: string; label: string } | null {
  const key = match.weather_localized_key;
  if (key && WEATHER_CONDITION_ZH[key]) {
    return { icon: weatherIconFor(key), label: WEATHER_CONDITION_ZH[key] };
  }
  const desc = match.weather_description;
  if (desc) {
    const hit = WEATHER_KEYWORDS.find(([re]) => re.test(desc));
    return hit ? { icon: hit[1], label: hit[2] } : { icon: "🌤", label: desc };
  }
  return null;
}

function venueLine(match: Match): string | null {
  const parts = [match.venue_name, match.venue_city, match.venue_country].filter(
    (p): p is string => !!p,
  );
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** 球场经纬度齐全时的 Google Maps 链接(FotMob 网页版同款拼法,2026-08-24
 * 实网核对);任一缺失不渲染,绝不用 0 兜底(0,0 是几内亚湾,不是"未知")。 */
function mapsUrl(match: Match): string | null {
  const { venue_lat: lat, venue_long: lng } = match;
  if (lat == null || lng == null) return null;
  return `https://www.google.com/maps/search/${lat},${lng}/@${lat},${lng}&map_action=map`;
}

/** 千分位(23576 → 23,576)。不用 toLocaleString——那会触发时区展示纪律
 * 扫描(tests/timezone-discipline.test.ts),纯数字格式化用正则即可。 */
function thousands(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function weatherLine(match: Match): { icon: string; text: string } | null {
  const parts: string[] = [];
  let icon = "🌤";
  const wt = weatherText(match);
  if (wt) {
    icon = wt.icon;
    parts.push(wt.label);
  }
  if (match.temperature_c != null) parts.push(`${match.temperature_c}°C`);
  if (match.wind_speed_kmh != null) parts.push(`风速 ${match.wind_speed_kmh} km/h`);
  return parts.length > 0 ? { icon, text: parts.join(" · ") } : null;
}

export function MatchInfoCard({ match }: { match: Match }) {
  const venue = venueLine(match);
  const weather = weatherLine(match);
  const capacity = match.venue_capacity;
  const surface = match.venue_surface;
  const maps = mapsUrl(match);

  if (!venue && !weather && capacity == null && !surface) return null;

  return (
    <section className={styles.card} aria-label="比赛信息" data-testid="match-info-card">
      <h3 className={styles.title}>比赛信息</h3>
      <dl className={styles.rows}>
        {venue && (
          <div>
            <dt>
              <span aria-hidden>🏟</span> 球场
            </dt>
            <dd>
              {maps ? (
                <a
                  className={styles.mapLink}
                  href={maps}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`在地图中查看 ${match.venue_name ?? "球场"}`}
                >
                  {venue}
                </a>
              ) : (
                venue
              )}
            </dd>
          </div>
        )}
        {capacity != null && (
          <div>
            <dt>容纳人数</dt>
            <dd className="num">{thousands(capacity)}</dd>
          </div>
        )}
        {surface && (
          <div>
            <dt>场地表面</dt>
            <dd>{VENUE_SURFACE_ZH[surface] ?? surface}</dd>
          </div>
        )}
        {weather && (
          <div>
            <dt>
              <span aria-hidden>{weather.icon}</span> 天气
            </dt>
            <dd>{weather.text}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
