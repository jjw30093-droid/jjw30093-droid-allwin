"use client";

/**
 * 赔率时间轴(比赛详情页赔率 tab)。
 *
 * 会话 cookie Path=/api/v1,只有浏览器端请求能携带 → 本组件用 clientFetch。
 * 2026-08-16 权限口径修正:后端对任何人(含匿名)恒返回完整快照时间线
 * (MatchOddsAvailableDTO.tier 已收窄成常量 "full"),不再有身份分层。
 *
 * 2026-08-14 重设计(Claude Design 定稿)留下的展示形态判据仍然有效——当样本
 * 不足以画走势时(`display_mode === "current_odds"`)不出表格,改成"大数字
 * 快照 + 一行说明",不假装有走势可看;真有走势(`display_mode ===
 * "odds_changes"`)时,大数字块之上加折线图,之下保留原有"每公司一行+完整
 * 历史折叠"表格——这条判据现在只取决于样本量,不再叠加 tier 身份判断。
 *
 * 文案纪律:只写"系统于 X 检测到",不写因果;时间按北京时间展示(CLAUDE.md §11.2)。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { clientFetch } from "@/lib/api-v1";
import { EChart } from "@/components/EChart";
import { LocalTime } from "./LocalTime";
import { formatBeijingZh, LEGACY_SOURCE_ZH, MARKET_FIELDS, MARKET_ZH } from "./zh";
import { flatOddsGroup, type OddsResponse, type OddsSnapshot } from "./types";
import styles from "./OddsTimeline.module.css";

const PHASE_ZH: Record<string, string> = {
  pre_match: "赛前",
  in_play: "滚球",
  unknown: "未标注",
};

/** 主题切换时重新解析一遍 CSS 变量的实际取值,ECharts(canvas)拿不到 var()。 */
const THEME_CHANGE_EVENT = "allwin-theme-change";

function groupLabel(s: OddsSnapshot): string {
  return `${s.market}|${s.company_id}`;
}

/** 嵌套 payload 的 initial/latest 原样拆开(不像 flatOddsGroup 那样只留一个)。 */
function splitInitialLatest(
  payload: OddsSnapshot["payload"],
): { initial: Record<string, number> | null; latest: Record<string, number> | null } {
  if (payload == null || typeof payload !== "object") return { initial: null, latest: null };
  if ("latest" in payload || "initial" in payload) {
    const nested = payload as { initial: Record<string, number> | null; latest: Record<string, number> | null };
    const latest = nested.latest && typeof nested.latest === "object" ? nested.latest : null;
    const initial = nested.initial && typeof nested.initial === "object" ? nested.initial : null;
    return { initial, latest: latest ?? initial };
  }
  return { initial: null, latest: payload as Record<string, number> };
}

export type CompanyOddsRow = {
  companyId: string;
  companyLabel: string;
  marketPhase: string;
  observedAt: string;
  sourceUpdatedAt: string | null | undefined;
  /** 非 null 且与 current 有差异时,才是真实movement——前端据此决定是否画箭头。 */
  initial: Record<string, number> | null;
  current: Record<string, number> | null;
  changed: boolean;
};

/**
 * 把同一公司同一市场的全部快照行归并成一行"初盘→最新"摘要。
 *
 * 旧逻辑(bug 见 2026-08-12 审计):不管几条快照,永远只挑最早一条打「初盘」
 * 标签、最晚一条打「最新」标签,但取值都经 flatOddsGroup(优先 latest)——
 * 结果「初盘」那一行显示的其实是 latest 的数字,真实的开盘价从未展示;
 * 单一快照(current_odds 模式,79% 的赛前比赛是这种)甚至只有一行「初盘」,
 * payload 里 initial≠latest 的真实盘口变化(如 Crown 2.85→2.83)完全消失。
 *
 * 新逻辑:每家公司只出一行,该行同时携带 initial 与 current 两个值——
 * 有嵌套 payload 时直接拆出;没有(扁平/历史数据)时,只有该公司出现过
 * 多条快照才能拿最早一条的值当 initial 的近似(单条扁平快照没有"变化"
 * 可言,initial 保持 null)。时间戳统一用最新一条快照的 observed_at——
 * 我们没有单独记录"初盘是什么时候观测到的",不虚构第二个时间戳。
 */
export function summarizeCompanyOdds(
  rows: OddsSnapshot[],
  fieldKeys: string[],
): CompanyOddsRow {
  const sorted = [...rows].sort((a, b) => a.observed_at.localeCompare(b.observed_at));
  const freshest = sorted[sorted.length - 1];
  const earliest = sorted[0];
  const nested = splitInitialLatest(freshest.payload);
  const initialCandidate =
    nested.initial ?? (sorted.length > 1 ? flatOddsGroup(earliest.payload) : null);
  const current = nested.latest ?? flatOddsGroup(freshest.payload);
  const changed =
    initialCandidate != null &&
    current != null &&
    fieldKeys.some(
      (k) => initialCandidate[k] != null && current[k] != null && initialCandidate[k] !== current[k],
    );
  return {
    companyId: freshest.company_id,
    companyLabel: freshest.company_name || freshest.company_id,
    marketPhase: freshest.market_phase,
    observedAt: freshest.observed_at,
    sourceUpdatedAt: freshest.source_updated_at,
    initial: changed ? initialCandidate : null,
    current,
    changed,
  };
}

type ChartColors = { axis: string; grid: string; win: string; draw: string; loss: string };

/** 完整时间线时,为快照最多的一家公司的 1x2 序列画折线(≥2 个点才画) */
function build1x2Chart(
  snapshots: OddsSnapshot[],
  colors: ChartColors,
): { option: EChartsOption; summary: string } | null {
  const byCompany = new Map<string, OddsSnapshot[]>();
  for (const s of snapshots) {
    // flatOddsGroup:兼容扁平(历史回填,73.5 万行)与嵌套(实时轮询)两种
    // payload 形状——旧代码只认 payload.latest,扁平数据全部被跳过,
    // 1x2 走势图因此从不渲染(审计 B2)。
    if (s.market !== "1x2" || !flatOddsGroup(s.payload)) continue;
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
  const pick = (key: string) =>
    series.map((s) => flatOddsGroup(s.payload)?.[key] ?? null);
  const company = series[0].company_name || series[0].company_id;
  const first = times[0];
  const last = times[times.length - 1];
  const option: EChartsOption = {
    grid: { left: 44, right: 16, top: 30, bottom: 28 },
    legend: {
      data: ["主胜", "平局", "客胜"],
      textStyle: { color: colors.axis },
      top: 0,
    },
    xAxis: {
      type: "category",
      data: times,
      axisLabel: { color: colors.axis, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: colors.axis },
      splitLine: { lineStyle: { color: colors.grid } },
    },
    series: [
      { name: "主胜", type: "line", data: pick("home"), color: colors.win },
      { name: "平局", type: "line", data: pick("draw"), color: colors.draw },
      { name: "客胜", type: "line", data: pick("away"), color: colors.loss },
    ],
  };
  return {
    option,
    summary: `${company} 胜平负即时赔率随观察时间的变化(单位:欧洲赔率;时间范围:北京时间 ${first} 至 ${last},共 ${series.length} 个快照)`,
  };
}

/** 一家公司一个市场的快照数字块:大数字 + 小标签,取代原来的表格行。 */
function OddsNumbers({ market, row }: { market: string; row: CompanyOddsRow }) {
  const fields = MARKET_FIELDS[market] ?? [];
  const values = row.current ?? row.initial;
  return (
    <div className={styles.numsBlock}>
      <div className={styles.numsGrid}>
        {fields.map((f) => (
          <div key={f.key} className={styles.numCell}>
            <span className="num">{values?.[f.key] ?? "—"}</span>
            <span>{f.label}</span>
          </div>
        ))}
      </div>
      <p className={styles.sourceLine}>
        <span>{row.companyLabel}</span>
        <span aria-hidden> · </span>
        <span>{MARKET_ZH[market] ?? market}</span>
        <span aria-hidden> · </span>
        <span>{PHASE_ZH[row.marketPhase] ?? row.marketPhase}</span>
        <span aria-hidden> · </span>
        {/* 这一行赔率具体是哪个时刻观测到的——每家公司各自的 observed_at,
            不是页面级共享一个新鲜度时间(不同公司同一市场的最后观测时间
            可能不一样)。 */}
        <span>
          <LocalTime iso={row.observedAt} />
        </span>
      </p>
    </div>
  );
}

export function OddsTimeline({ matchId }: { matchId: number }) {
  const [resp, setResp] = useState<OddsResponse | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [themeTick, setThemeTick] = useState(0);

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

  useEffect(() => {
    const bump = () => setThemeTick((t) => t + 1);
    window.addEventListener(THEME_CHANGE_EVENT, bump);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, bump);
  }, []);

  const retry = useCallback(() => {
    setError(false);
    setAttempt((n) => n + 1);
  }, []);

  const isFullTimeline =
    resp?.available === true &&
    resp.tier === "full" &&
    resp.coverage_tier === "full_timeline" &&
    resp.display_mode === "odds_changes";

  const chart = useMemo(() => {
    if (!isFullTimeline || resp?.available !== true) return null;
    const style = getComputedStyle(document.documentElement);
    const readVar = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    const colors: ChartColors = {
      axis: readVar("--ink-3", "#82969d"),
      grid: readVar("--border", "#203842"),
      win: readVar("--win", "#68c994"),
      draw: readVar("--draw", "#aaa79f"),
      loss: readVar("--loss", "#ef7865"),
    };
    return build1x2Chart(resp.snapshots, colors);
    // themeTick 只用来触发重新读取 CSS 变量,不直接参与计算
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resp, isFullTimeline, themeTick]);

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
          {resp.note ?? "本场为历史存档赔率,仅有初盘与临场两点,无完整走势时间线。"}
          {" "}
          展示初盘与临场两点。
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
                        <td>
                          {p.provider}
                          {/* 同一公司可能出现在多个存档批次里,数值可能不同——
                              标批次来源,不悄悄去重(见 zh.ts LEGACY_SOURCE_ZH 注释)。 */}
                          <span className={styles.sourceTag}>
                            {LEGACY_SOURCE_ZH[p.source] ?? p.source}
                          </span>
                        </td>
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
  const observationCount = resp.observation_count ?? resp.snapshots.length;

  // 每个市场归并出的公司行,数字块(简化态/完整态都要用)按这份数据渲染
  const marketRows = markets.map((market) => {
    const rows = resp.snapshots.filter((s) => s.market === market);
    const fieldKeys = (MARKET_FIELDS[market] ?? []).map((f) => f.key);
    const byCompany = new Map<string, typeof rows>();
    for (const snap of rows) {
      const list = byCompany.get(snap.company_id) ?? [];
      list.push(snap);
      byCompany.set(snap.company_id, list);
    }
    const companyRows = Array.from(byCompany.values()).map((list) =>
      summarizeCompanyOdds(list, fieldKeys),
    );
    return { market, rows, companyRows };
  });
  const freshestRow = marketRows[0]?.companyRows[0] ?? null;

  if (!isFullTimeline) {
    // 简化态(样本不足以画走势):只给数字块 + 说明,不出表格。
    return (
      <div>
        {marketRows.map(({ market, companyRows }) => (
          <div key={market} className={styles.marketBlock}>
            {companyRows.map((row) => (
              <OddsNumbers key={`${market}|${row.companyId}`} market={market} row={row} />
            ))}
          </div>
        ))}
        {freshestRow && (
          <p className={styles.snapshotNote}>
            这是 <LocalTime iso={freshestRow.observedAt} /> 采集到的快照,
            <b>不是实时赔率</b>。本场只采集到 <span className="num">{observationCount}</span>{" "}
            个观测点,不足以画走势,所以这里不画曲线。
          </p>
        )}
      </div>
    );
  }

  const header = (fields: { key: string; label: string }[]) => (
    <thead>
      <tr>
        <th>公司</th>
        <th>阶段</th>
        {fields.map((f) => (
          <th key={f.key}>{f.label}</th>
        ))}
        <th>本站采集时间</th>
        <th>来源声明时间</th>
      </tr>
    </thead>
  );

  return (
    <div>
      <p className={styles.tierNote}>
        赔率变化记录:默认展示每家公司的初盘→最新,完整历史可展开。
        完整快照时间线(已登录):同一公司同一市场,内容有变化才记录一条。
        {resp.home_away_inverted &&
          " 注:该场来源主客方向与本站相反,数值已按本站主客口径换算。"}
      </p>

      {chart && (
        <div className={styles.chartWrap}>
          <EChart option={chart.option} height={220} ariaSummary={chart.summary} />
        </div>
      )}

      {marketRows.map(({ market, companyRows }) => (
        <div key={market} className={styles.marketBlock}>
          {companyRows.map((row) => (
            <OddsNumbers key={`${market}|${row.companyId}`} market={market} row={row} />
          ))}
        </div>
      ))}

      {marketRows.map(({ market, rows, companyRows }) => {
        const fields = MARKET_FIELDS[market] ?? [];
        const rawRow = (snap: OddsSnapshot, key: string) => {
          const g = flatOddsGroup(snap.payload);
          return (
            <tr key={key}>
              <td>{snap.company_name || snap.company_id}</td>
              <td>{PHASE_ZH[snap.market_phase] ?? snap.market_phase}</td>
              {fields.map((f) => (
                <td key={f.key} className="num">
                  {g?.[f.key] != null ? g[f.key] : "—"}
                </td>
              ))}
              <td>
                <LocalTime iso={snap.observed_at} />
              </td>
              <td>
                <LocalTime iso={snap.source_updated_at} fallback="未声明" />
              </td>
            </tr>
          );
        };
        return (
          <div key={market} className={styles.marketBlock}>
            <h4 className={styles.marketTitle}>{MARKET_ZH[market] ?? market}</h4>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                {header(fields)}
                <tbody>
                  {companyRows.map((row) => (
                    <tr key={`${market}|${row.companyId}`}>
                      <td>
                        {row.companyLabel}
                        {row.changed && <span className={styles.pointTag}>有变动</span>}
                      </td>
                      <td>{PHASE_ZH[row.marketPhase] ?? row.marketPhase}</td>
                      {fields.map((f) => {
                        const cur = row.current?.[f.key];
                        const init = row.initial?.[f.key];
                        return (
                          <td key={f.key} className="num">
                            {row.changed && init != null && cur != null
                              ? `${init} → ${cur}`
                              : (cur ?? init ?? "—")}
                          </td>
                        );
                      })}
                      <td>
                        <LocalTime iso={row.observedAt} />
                      </td>
                      <td>
                        <LocalTime iso={row.sourceUpdatedAt} fallback="未声明" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length > companyRows.length && (
              <details className={styles.historyDetails}>
                <summary className={styles.historySummary}>
                  查看完整历史({rows.length} 条)
                </summary>
                <div className={styles.tableWrap}>
                  <table className={styles.table}>
                    {header(fields)}
                    <tbody>
                      {rows.map((snap, i) =>
                        rawRow(snap, `${groupLabel(snap)}|${snap.observed_at}|${i}`),
                      )}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </div>
        );
      })}

      <p className={styles.footNote}>
        「本站采集时间」为本站首次观察到该数值的时间(observed_at,按北京时间显示);
        来源未声明更新时间时如实标注「未声明」。赔率数据仅为同时段观察记录,不构成任何投注建议。
      </p>
    </div>
  );
}
