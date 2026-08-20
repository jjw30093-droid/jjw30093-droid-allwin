/**
 * 比赛信息卡:球场、天气、主裁(2026-08-20,参照本地 miaomiaodi.cc 比赛详情页
 * 的对应板块;数据来自 FotMob 赛前抓取,见 backend/fotmob_client.py::
 * parse_match_dim)。
 *
 * 三行各自独立判空——产线真实覆盖率:裁判约 71%,温度/风速约 16%,场馆/天气
 * 描述是本次新增列,历史比赛恒为空。三行全空时整卡不渲染(不留一个空框)。
 */

import type { MatchDetailResponse } from "@/lib/api-v1";
import styles from "./MatchInfoCard.module.css";

type Match = MatchDetailResponse["match"];

/** 天气描述英文原文 → 中文类别 + 图标,关键词命中即返回;命不中时如实展示
 * 英文原文,不猜译文(CLAUDE.md §2.2)。顺序即优先级:一段描述常常同时提到
 * 风和云("Partly Cloudy/Wind"),排在前面的关键词先命中。 */
const WEATHER_KEYWORDS: Array<[RegExp, string, string]> = [
  [/thunder/i, "⛈", "雷雨"],
  [/snow/i, "🌨", "雪"],
  [/rain|shower|drizzle/i, "🌧", "雨"],
  [/fog|mist|haze/i, "🌫", "雾"],
  [/wind/i, "💨", "大风"],
  [/cloud|overcast/i, "⛅", "多云"],
  [/clear|sunny/i, "☀", "晴"],
];

function weatherZh(description: string): { icon: string; text: string } {
  const hit = WEATHER_KEYWORDS.find(([re]) => re.test(description));
  return hit ? { icon: hit[1], text: hit[2] } : { icon: "🌤", text: description };
}

function venueLine(match: Match): string | null {
  const parts = [match.venue_name, match.venue_city, match.venue_country].filter(
    (p): p is string => !!p,
  );
  return parts.length > 0 ? parts.join(" · ") : null;
}

function weatherLine(match: Match): { icon: string; text: string } | null {
  const parts: string[] = [];
  let icon = "🌤";
  if (match.weather_description) {
    const zh = weatherZh(match.weather_description);
    icon = zh.icon;
    parts.push(zh.text);
  }
  if (match.temperature_c != null) parts.push(`${match.temperature_c}°C`);
  if (match.wind_speed_kmh != null) parts.push(`风速 ${match.wind_speed_kmh} km/h`);
  return parts.length > 0 ? { icon, text: parts.join(" · ") } : null;
}

export function MatchInfoCard({ match }: { match: Match }) {
  const venue = venueLine(match);
  const weather = weatherLine(match);
  const referee = match.referee;

  if (!venue && !weather && !referee) return null;

  return (
    <section className={styles.card} aria-label="比赛信息" data-testid="match-info-card">
      <h3 className={styles.title}>比赛信息</h3>
      <dl className={styles.rows}>
        {venue && (
          <div>
            <dt>
              <span aria-hidden>🏟</span> 球场
            </dt>
            <dd>{venue}</dd>
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
        {referee && (
          <div>
            <dt>主裁</dt>
            <dd>{referee}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
