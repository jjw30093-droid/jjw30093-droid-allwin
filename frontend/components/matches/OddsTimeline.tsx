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
import { formatOdds } from "@/lib/format";
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

/** 赔率/盘口线防御性去噪——修正存储层偶发的 IEEE754 尾部误差(真实事故
 * 2026-08-21:某场比赛显示"1.9300000000000002")。后端
 * backend/queries/odds.py::legacy_summary_points 已经在读侧做过同一处理,
 * 这里是前端侧的第二道防线,防止任何未经过该函数的数据源把噪声带到页面。
 * 去噪后的干净数值交给 renderOddsNum() 统一补零,不在这里决定小数位数。 */
function cleanOddsNum(v: number | null | undefined): number | null {
  if (v == null || Number.isNaN(v)) return null;
  return Math.round(v * 100) / 100;
}

/** 渲染用:去噪 + 固定两位小数补零(2026-08-23 起赔率与盘口线统一按这个
 * 格式显示,不再保留"干净整数值不补零"的例外——生产实测出现过"4.1""6"
 * "1"这类末位缺零的写法,与同一行里补零过的数字混排,反而更不统一)。 */
function renderOddsNum(v: number | null | undefined): string {
  const cleaned = cleanOddsNum(v);
  return cleaned == null ? "—" : formatOdds(cleaned);
}

/** 亚洲让球盘口线方向标签——符号约定已验证(docs/data-sources.md §2.5,
 * 48 组精确配对 + 2,834 组历史样本交叉核对,与
 * backend/commands/reco_settlement_math.py::_resolve_ah 同一套约定):
 * line>0 主队让球(主队热门)、line<0 客队让球(客队热门)、line=0 平手盘。
 * 只用于 market==="ah";大小球(ou/corners_ou)的 line 是入球数门槛,没有
 * 主客方向这个概念,不适用这套标签。 */
function ahDirectionZh(line: number | null | undefined): "主让" | "客让" | "平手" | null {
  if (line == null || Number.isNaN(line)) return null;
  if (line > 0) return "主让";
  if (line < 0) return "客让";
  return "平手";
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
    // 2026-08-26:initial 恒保留(不再在未变时置 null)——两行「初盘/最新」
    // 展示要能显示"这家没动"这个真实信息(初盘==最新),而不是把它和"只抓到
    // 一条快照、根本没有初盘可比"(initialCandidate 本就为 null)混为一谈。
    // 涨跌方向改由渲染层逐字段调用 oddsDelta() 判定,不再依赖这个布尔。
    initial: initialCandidate,
    current,
    changed,
  };
}

/** 单个赔率字段的涨跌:与初盘比,量化到 2 位小数。
 * dir="unknown" 专指"没有初盘可比"(单条快照),与"有初盘且没动"(flat)是
 * 两件不同的事,不能混——前者不画方向,后者要如实标"持平"。 */
export type OddsDir = "up" | "down" | "flat" | "unknown";
export function oddsDelta(
  initial: number | null | undefined,
  current: number | null | undefined,
): { dir: OddsDir; delta: number } {
  const i = cleanOddsNum(initial);
  const c = cleanOddsNum(current);
  if (i == null || c == null) return { dir: "unknown", delta: 0 };
  const d = Math.round((c - i) * 100) / 100;
  if (d === 0) return { dir: "flat", delta: 0 };
  return { dir: d > 0 ? "up" : "down", delta: d };
}

/** 带符号两位小数,负号用真正的 U+2212 减号(与表格等宽数字对齐更整齐)。 */
export function formatDelta(delta: number): string {
  return (delta > 0 ? "+" : "−") + Math.abs(delta).toFixed(2);
}

/** 每个市场"最有代表性的那条水位/赔率",聚合升降摘要按它计数。
 * 1x2 取主胜赔率(最被关注的单一数字),让球/大小取上盘(主队/大球)水位。
 * label 会原样进用户可见文案,所以是"主胜""主队水位"这种完整词,不是字段名。 */
export const ODDS_PRIMARY_FIELD: Record<string, { key: string; label: string }> = {
  "1x2": { key: "home", label: "主胜" },
  ah: { key: "home", label: "主队水位" },
  ou: { key: "over", label: "大球水位" },
  corners_ou: { key: "over", label: "大球水位" },
};

export type MarketMovement = { up: number; down: number; flat: number; unknown: number; total: number };

/** 一个市场里,各公司的代表字段相对初盘涨/跌/平/无初盘 各多少家。
 * 纯计数、纯描述统计——不做"说明市场看好谁"这类归因(§2.1 文案纪律)。 */
export function summarizeMarketMovement(
  rows: CompanyOddsRow[],
  primaryKey: string,
): MarketMovement {
  const m: MarketMovement = { up: 0, down: 0, flat: 0, unknown: 0, total: rows.length };
  for (const r of rows) {
    m[oddsDelta(r.initial?.[primaryKey], r.current?.[primaryKey]).dir] += 1;
  }
  return m;
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

/** 一个数字单元格:数值 + 可选的涨跌方向注释(箭头 + 带符号幅度)。
 * 主数值恒用 --ink(中性、权威),不被方向色染——方向色只落在小号注释上,
 * 避免和 §11.2 里青绿="选中"的语义在密集表格里打架;方向另由 ↑/↓ 箭头和
 * +/− 符号双通道编码,色盲用户不依赖颜色也能读。 */
function OddsCell({
  value,
  initial,
  showDelta,
}: {
  value: number | null | undefined;
  initial: number | null | undefined;
  showDelta: boolean;
}) {
  const { dir, delta } = oddsDelta(initial, value);
  return (
    <span className={styles.oCell} data-dir={showDelta ? dir : "unknown"}>
      <span className={`num ${styles.oNum}`}>{renderOddsNum(value)}</span>
      {showDelta && dir !== "unknown" && (
        <span className={styles.oDelta} aria-hidden>
          {dir === "flat" ? "—" : (dir === "up" ? "↑" : "↓") + formatDelta(delta).slice(1)}
        </span>
      )}
    </span>
  );
}

/** 一家公司一行(内含"初盘/最新"两子行)。有初盘可比时两行;只有单条快照
 * (initial 缺失)时退化成单行"最新",不假装有初盘。 */
function CompanyRow({ market, row }: { market: string; row: CompanyOddsRow }) {
  const fields = MARKET_FIELDS[market] ?? [];
  const hasInitial = row.initial != null;
  const lineTag =
    market === "ah" ? ahDirectionZh(row.current?.line ?? row.initial?.line) : null;
  return (
    <div className={styles.coRow} data-two={hasInitial}>
      <div className={styles.coName}>
        <span className={styles.coLabel}>{row.companyLabel}</span>
        {lineTag && <span className={styles.ahTag}>{lineTag}</span>}
        <span className={styles.coTime}>
          <LocalTime iso={row.observedAt} />
        </span>
      </div>
      {hasInitial && (
        <>
          <span className={styles.kind}>初盘</span>
          {fields.map((f) => (
            <span key={`i-${f.key}`} className={`num ${styles.initNum}`}>
              {renderOddsNum(row.initial?.[f.key])}
            </span>
          ))}
        </>
      )}
      <span className={styles.kind}>{hasInitial ? "最新" : ""}</span>
      {fields.map((f) => (
        <OddsCell
          key={`c-${f.key}`}
          value={row.current?.[f.key]}
          initial={row.initial?.[f.key]}
          showDelta={hasInitial}
        />
      ))}
    </div>
  );
}

/** 一个市场的完整块:聚合升降摘要 + 表头 + 各公司行。 */
function MarketBlock({
  market,
  companyRows,
  phaseNote,
}: {
  market: string;
  companyRows: CompanyOddsRow[];
  phaseNote: string | null;
}) {
  const fields = MARKET_FIELDS[market] ?? [];
  const primary = ODDS_PRIMARY_FIELD[market];
  const move = primary ? summarizeMarketMovement(companyRows, primary.key) : null;
  const hasMovement = move != null && move.up + move.down + move.flat > 0;
  return (
    <div className={styles.marketBlock}>
      <div className={styles.sumBar}>
        <span className={styles.sumTotal}>{companyRows.length} 家公司</span>
        {hasMovement && move && primary && (
          <span className={styles.sumMove}>
            {primary.label}
            <span className={styles.sumCnt} data-dir="up">
              <i className={styles.bar} /> {move.up} 家上调
            </span>
            <span className={styles.sumCnt} data-dir="flat">
              <i className={styles.bar} /> {move.flat} 家不变
            </span>
            <span className={styles.sumCnt} data-dir="down">
              <i className={styles.bar} /> {move.down} 家下调
            </span>
          </span>
        )}
        {phaseNote && <span className={styles.sumPhase}>{phaseNote}</span>}
      </div>
      <div className={styles.grid} role="table" aria-label={`${MARKET_ZH[market] ?? market}赔率`}>
        <div className={styles.gridHead} role="row">
          <span role="columnheader">公司</span>
          <span role="columnheader" aria-hidden />
          {fields.map((f) => (
            <span key={f.key} role="columnheader">
              {f.label}
            </span>
          ))}
        </div>
        {companyRows.map((row) => (
          <CompanyRow key={`${market}|${row.companyId}`} market={market} row={row} />
        ))}
      </div>
    </div>
  );
}

/** 市场切换胶囊(只列出该场真实存在的市场)。 */
function MarketTabs({
  markets,
  active,
  onSelect,
}: {
  markets: string[];
  active: string;
  onSelect: (m: string) => void;
}) {
  return (
    <div className={styles.tabs} role="tablist" aria-label="赔率市场">
      {markets.map((m) => (
        <button
          key={m}
          type="button"
          role="tab"
          aria-selected={m === active}
          className={m === active ? styles.tabOn : styles.tab}
          onClick={() => onSelect(m)}
        >
          {MARKET_ZH[m] ?? m}
        </button>
      ))}
    </div>
  );
}

export function OddsTimeline({ matchId }: { matchId: number }) {
  const [resp, setResp] = useState<OddsResponse | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [themeTick, setThemeTick] = useState(0);
  // 选中的市场 tab。存"用户点过的那个",真正生效的 active 在 render 里用
  // markets.includes() 兜底解析(市场集合随数据变化,存 null 时回落到第一个)。
  const [pickedMarket, setPickedMarket] = useState<string | null>(null);

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
                            <td className="num">{renderOddsNum(p.home_or_over)}</td>
                            <td className="num">{renderOddsNum(p.draw)}</td>
                            <td className="num">{renderOddsNum(p.away_or_under)}</td>
                          </>
                        ) : (
                          <>
                            <td className="num">{renderOddsNum(p.home_or_over)}</td>
                            <td className="num">
                              {renderOddsNum(p.line)}
                              {market === "ah" && ahDirectionZh(p.line) && (
                                <span className={styles.ahTag}>{ahDirectionZh(p.line)}</span>
                              )}
                            </td>
                            <td className="num">{renderOddsNum(p.away_or_under)}</td>
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

  const observationCount = resp.observation_count ?? resp.snapshots.length;

  // 市场顺序固定为 1x2 → ah → ou → corners_ou(用户熟悉的排序),只保留该场
  // 真实存在的。每个市场按公司归并出"初盘/最新"行。
  const MARKET_ORDER = ["1x2", "ah", "ou", "corners_ou"];
  const present = new Set(resp.snapshots.map((s) => s.market));
  const markets = MARKET_ORDER.filter((m) => present.has(m));
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
    return { market, companyRows };
  });

  // 生效的市场:用户点过且仍存在则用它,否则回落到第一个(市场集合变化时的兜底)。
  const active = pickedMarket && markets.includes(pickedMarket) ? pickedMarket : markets[0];
  const activeBlock = marketRows.find((b) => b.market === active) ?? marketRows[0];

  // 「阶段」在整批快照里如果只有一个真实取值(几乎恒为"赛前"),就并进摘要条
  // 一句话,不再占一列;真出现差异(如混入 in_play)时,退回不展示统一阶段句,
  // 由 CompanyRow 各自的时间承担——不为了好看丢真实差异(§2.1)。
  const allPhases = new Set(resp.snapshots.map((s) => s.market_phase));
  const uniformPhase = allPhases.size === 1 ? [...allPhases][0] : null;
  const phaseNote = uniformPhase != null ? (PHASE_ZH[uniformPhase] ?? uniformPhase) : null;

  return (
    <div>
      <MarketTabs markets={markets} active={active} onSelect={setPickedMarket} />

      {/* 走势图只在 1x2 市场、且样本足够(odds_changes)时出现;build1x2Chart
          是 1x2 专用,其它市场的 tab 不画图。图注里点名画的是哪家公司。 */}
      {chart && active === "1x2" && (
        <div className={styles.chartWrap}>
          <EChart option={chart.option} height={220} ariaSummary={chart.summary} />
        </div>
      )}

      {activeBlock && (
        <MarketBlock
          market={activeBlock.market}
          companyRows={activeBlock.companyRows}
          phaseNote={phaseNote}
        />
      )}

      {!isFullTimeline && (
        <p className={styles.snapshotNote}>
          这些是<b>目前抓到的最新赔率,不是实时刷新</b>。这场只观测到{" "}
          <span className="num">{observationCount}</span> 个时点,还画不出完整走势。
        </p>
      )}

      <p className={styles.footNote}>
        每家公司后面的时间是我们第一次看到该数字的时间(北京时间);「上调/下调」按各公司
        相对自己初盘的变化计,数值为幅度。
        {resp.home_away_inverted &&
          " 该场来源主客方向与本站相反,数值已按本站主客口径换算。"}
      </p>
    </div>
  );
}
