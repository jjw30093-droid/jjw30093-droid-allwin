"use client";

/**
 * 赔率时间轴(比赛详情页第 5 区块)。
 *
 * 会话 cookie Path=/api/v1,只有浏览器端请求能携带 → 本组件用 clientFetch:
 * - 匿名/Free/Pro:后端返回延迟摘要(每公司每市场最新一条,观察时间 ≥1 小时前);
 * - Premium(odds:history_full):完整快照时间线,1x2 市场补一张折线图。
 *
 * 文案纪律:只写"系统于 X 检测到",不写因果;时间按北京时间展示(CLAUDE.md §11.2)。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { clientFetch } from "@/lib/api-v1";
import { EChart } from "@/components/EChart";
import { LocalTime } from "./LocalTime";
import { formatBeijingZh, MARKET_FIELDS, MARKET_ZH } from "./zh";
import type { OddsResponse, OddsSnapshot } from "./types";
import styles from "./OddsTimeline.module.css";

const PHASE_ZH: Record<string, string> = {
  pre_match: "赛前",
  in_play: "滚球",
  unknown: "未标注",
};

function groupLabel(s: OddsSnapshot): string {
  return `${s.market}|${s.company_id}`;
}

/** 完整时间线时,为快照最多的一家公司的 1x2 序列画折线(≥2 个点才画) */
function build1x2Chart(
  snapshots: OddsSnapshot[],
): { option: EChartsOption; summary: string } | null {
  const byCompany = new Map<string, OddsSnapshot[]>();
  for (const s of snapshots) {
    if (s.market !== "1x2" || !s.payload.latest) continue;
    const list = byCompany.get(s.company_id) ?? [];
    list.push(s);
    byCompany.set(s.company_id, list);
  }
  let best: OddsSnapshot[] | null = null;
  for (const list of byCompany.values()) {
    if (list.length >= 2 && (best == null || list.length > best.length)) {
      best = list;
    }
  }
  if (!best) return null;
  const series = best;
  // 北京时间(与下方表格的 LocalTime 一致)——曾用浏览器本地时区,会让同一
  // 组件里图表横轴和表格行显示两套不同的时钟,对中文用户反而更迷惑。
  const times = series.map((s) => formatBeijingZh(s.observed_at) ?? s.observed_at);
  const pick = (key: string) => series.map((s) => s.payload.latest?.[key] ?? null);
  const company = series[0].company_name || series[0].company_id;
  const first = times[0];
  const last = times[times.length - 1];
  const option: EChartsOption = {
    grid: { left: 44, right: 16, top: 30, bottom: 28 },
    legend: {
      data: ["主胜", "平局", "客胜"],
      textStyle: { color: "#a79c87" },
      top: 0,
    },
    xAxis: {
      type: "category",
      data: times,
      axisLabel: { color: "#a79c87", fontSize: 10 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "#a79c87" },
      splitLine: { lineStyle: { color: "#241c11" } },
    },
    series: [
      { name: "主胜", type: "line", data: pick("home"), color: "#4e9a5b" },
      { name: "平局", type: "line", data: pick("draw"), color: "#8a8069" },
      { name: "客胜", type: "line", data: pick("away"), color: "#c05437" },
    ],
  };
  return {
    option,
    summary: `${company} 胜平负即时赔率随观察时间的变化(单位:欧洲赔率;时间范围:本地时区 ${first} 至 ${last},共 ${series.length} 个快照)`,
  };
}

export function OddsTimeline({ matchId }: { matchId: number }) {
  const [resp, setResp] = useState<OddsResponse | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    clientFetch<OddsResponse>(`/api/v1/matches/${matchId}/odds`)
      .then((d) => {
        if (!cancelled) setResp(d);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, attempt]);

  const retry = useCallback(() => {
    setError(false);
    setAttempt((n) => n + 1);
  }, []);

  const chart = useMemo(
    () =>
      resp?.available && resp.tier === "full"
        ? build1x2Chart(resp.snapshots)
        : null,
    [resp],
  );

  if (error) {
    return (
      <div className={styles.stateBox}>
        赔率数据加载失败。
        <button type="button" onClick={retry} className={styles.retryBtn}>
          重试
        </button>
      </div>
    );
  }
  if (resp == null) {
    return (
      <div className={styles.skeleton} aria-label="赔率数据加载中">
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
      </div>
    );
  }
  if (!resp.available) {
    return <div className={styles.stateBox}>{resp.reason}</div>;
  }
  if (resp.coverage_tier === "open_close_only") {
    // 历史存档赔率:仅初盘+临场两点、无观测时间戳——只出表格,绝不画走势图。
    const pts = resp.summary_points ?? [];
    if (pts.length === 0) {
      return <div className={styles.stateBox}>该场比赛暂无可展示的赔率快照。</div>;
    }
    const legacyMarkets = Array.from(new Set(pts.map((p) => p.market)));
    const periodZh: Record<string, string> = { initial: "初盘", latest: "临场" };
    return (
      <div>
        <p className={styles.tierNote}>
          {resp.note ?? "本场为历史存档赔率,仅有初盘与临场两个观测点,无完整走势时间线。"}
          {" "}
          {resp.tier === "full"
            ? "Premium:展示初盘与临场两点。"
            : "免费/Pro:仅展示临场一点;初盘对比为 Premium 内容。"}
        </p>
        {legacyMarkets.map((market) => {
          const rows = pts.filter((p) => p.market === market);
          const is1x2 = market === "1x2";
          return (
            <div key={market} className={styles.marketBlock}>
              <h4 className={styles.marketTitle}>{MARKET_ZH[market] ?? market}</h4>
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>公司</th>
                      <th>阶段</th>
                      {is1x2 ? (
                        <>
                          <th>主胜</th>
                          <th>平局</th>
                          <th>客胜</th>
                        </>
                      ) : (
                        <>
                          <th>{market === "ah" ? "主队" : "大球"}</th>
                          <th>盘口线</th>
                          <th>{market === "ah" ? "客队" : "小球"}</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p, i) => (
                      <tr key={`${p.source}|${p.period}|${i}`}>
                        <td>{p.provider}</td>
                        <td>{periodZh[p.period] ?? p.period}</td>
                        {is1x2 ? (
                          <>
                            <td className="num">{p.home_or_over}</td>
                            <td className="num">{p.draw ?? "—"}</td>
                            <td className="num">{p.away_or_under}</td>
                          </>
                        ) : (
                          <>
                            <td className="num">{p.home_or_over}</td>
                            <td className="num">{p.line ?? "—"}</td>
                            <td className="num">{p.away_or_under}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </div>
    );
  }
  if (resp.snapshots.length === 0) {
    return (
      <div className={styles.stateBox}>该场比赛暂无可展示的赔率快照。</div>
    );
  }

  const markets = Array.from(new Set(resp.snapshots.map((s) => s.market)));

  return (
    <div>
      <p className={styles.tierNote}>
        {resp.display_mode === "current_odds"
          ? `当前赔率:目前共 ${resp.observation_count} 个系统观测点,不绘制虚假变化曲线。`
          : `赔率变化:目前共 ${resp.observation_count} 个系统观测点。`}
        {" "}
        {resp.tier === "full"
          ? "完整快照时间线(Premium):同一公司同一市场,内容有变化才记录一条。"
          : "延迟摘要(免费/Pro):每公司每市场仅展示观察时间在 1 小时前的最新一条;完整时间线为 Premium 内容。"}
        {resp.home_away_inverted &&
          " 注:该场来源主客方向与本站相反,数值已按本站主客口径换算。"}
      </p>

      {chart && (
        <div className={styles.chartWrap}>
          <EChart option={chart.option} height={220} ariaSummary={chart.summary} />
        </div>
      )}

      {markets.map((market) => {
        const fields = MARKET_FIELDS[market] ?? [];
        const rows = resp.snapshots.filter((s) => s.market === market);
        return (
          <div key={market} className={styles.marketBlock}>
            <h4 className={styles.marketTitle}>{MARKET_ZH[market] ?? market}</h4>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>公司</th>
                    <th>阶段</th>
                    {fields.map((f) => (
                      <th key={f.key}>{f.label}</th>
                    ))}
                    <th>系统检测时间</th>
                    <th>来源声明时间</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s, i) => {
                    const g = s.payload.latest ?? s.payload.initial;
                    return (
                      <tr key={`${groupLabel(s)}|${s.observed_at}|${i}`}>
                        <td>{s.company_name || s.company_id}</td>
                        <td>{PHASE_ZH[s.market_phase] ?? s.market_phase}</td>
                        {fields.map((f) => (
                          <td key={f.key} className="num">
                            {g?.[f.key] != null ? g[f.key] : "—"}
                          </td>
                        ))}
                        <td>
                          <LocalTime iso={s.observed_at} />
                        </td>
                        <td>
                          <LocalTime iso={s.source_updated_at} fallback="未声明" />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      <p className={styles.footNote}>
        「系统检测时间」为本系统首次观察到该数值的时间(observed_at,按你的时区显示);
        来源未声明更新时间时如实标注「未声明」。赔率数据仅为同时段观察记录,不构成任何投注建议。
      </p>
    </div>
  );
}
