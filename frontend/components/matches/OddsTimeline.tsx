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

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import type React from "react";
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

/** 市场展示顺序(用户熟悉的排序),只保留该场真实存在的。 */
const MARKET_ORDER = ["1x2", "ah", "ou", "corners_ou"];

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

export type HistoryEntry = {
  observedAt: string;
  values: Record<string, number> | null;
  /** 每个字段相对上一条(更早那条)的方向——首条(最早)全 unknown,是开盘点。 */
  dirs: Record<string, OddsDir>;
};

/** 一家公司在一个市场里的完整变化记录,最新在前。
 * 库里每条快照本身已是变化点(odds_snapshots.py hash-diff 去重,值不变不落库),
 * 所以这里不再二次去重;方向按时间升序逐条与前一条比后,整体倒序返回。
 * 缺失值(flatOddsGroup 返回 null 的行)不参与方向计算、也不污染下一条的基准。 */
export function buildCompanyHistory(
  snapshots: OddsSnapshot[],
  fieldKeys: string[],
): HistoryEntry[] {
  const asc = [...snapshots].sort((a, b) => a.observed_at.localeCompare(b.observed_at));
  const entries: HistoryEntry[] = [];
  let prev: Record<string, number> | null = null;
  for (const s of asc) {
    const values = flatOddsGroup(s.payload);
    const dirs: Record<string, OddsDir> = {};
    for (const k of fieldKeys) dirs[k] = oddsDelta(prev?.[k], values?.[k]).dir;
    entries.push({ observedAt: s.observed_at, values, dirs });
    if (values) prev = values; // 缺失行不更新基准,避免把"这条没抓到"当成"回到某值"
  }
  entries.reverse();
  return entries;
}

/** 下钻抽屉一次最多列多少条变化(最新在前);超过时如实标注总数,不静默截断。 */
export const HISTORY_LIMIT = 50;

type ChartColors = { axis: string; grid: string; win: string; draw: string; loss: string };

/** 该市场每家公司的原始快照,按 company_id 分组(不过滤,供图表选公司/抽屉复用)。 */
function groupByCompany(snapshots: OddsSnapshot[]): Map<string, OddsSnapshot[]> {
  const byCompany = new Map<string, OddsSnapshot[]>();
  for (const s of snapshots) {
    const list = byCompany.get(s.company_id) ?? [];
    list.push(s);
    byCompany.set(s.company_id, list);
  }
  return byCompany;
}

/** 该场比赛出现过的全部公司(跨所有市场,首次出现顺序),供筛选面板列出。 */
export function listCompanies(snapshots: OddsSnapshot[]): { id: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const s of snapshots) {
    if (!seen.has(s.company_id)) seen.set(s.company_id, s.company_name || s.company_id);
  }
  return Array.from(seen.entries()).map(([id, label]) => ({ id, label }));
}

/** 走势图画谁:优先跟随用户当前展开的公司(与下钻抽屉保持一致的"你正在看的
 * 就是图上这条线");该公司样本不足或被筛掉时,回落到"可见公司里样本最多的
 * 那家"(≥2 个点才够画一条线)。都没有则不画图。 */
export function pickChartCompany(
  rawByCompany: Map<string, OddsSnapshot[]>,
  preferredId: string | null,
  hiddenIds: Set<string>,
): string | null {
  if (preferredId && !hiddenIds.has(preferredId)) {
    const list = rawByCompany.get(preferredId);
    if (list && list.length >= 2) return preferredId;
  }
  let bestId: string | null = null;
  let bestLen = 0;
  for (const [id, list] of rawByCompany) {
    if (hiddenIds.has(id)) continue;
    if (list.length >= 2 && list.length > bestLen) {
      bestId = id;
      bestLen = list.length;
    }
  }
  return bestId;
}

/** 走势图最多画多少个点(2026-08-27,ah/ou 手机端可读性问题)。距开球越近
 * 采集越密(§6.3 最后 6 小时每小时一次),原始点数可以轻松破 200,窄屏手机
 * 上挤成一条看不出走势的毛线。超过这个数就走 downsampleForChart 分桶精简,
 * 不改变数据本身——分桶取"该窗口内最后一次观测到的值",与仓库里 FINAL
 * 快照"开球前最后一个有效快照"同一语义,不是插值/平均出来的假点。 */
const MAX_CHART_POINTS = 40;

/** 按时间把 series 分成最多 maxPoints 个桶,每桶只保留桶内时间最晚的那条
 * (即"这个时间窗口里最后已知的值")。最新(最后)一条真实观测值恒精确保留
 * 在输出末尾——它要跟表格"最新"那一行的值对得上;更早的点同样是真实
 * 观测(不是插值/平均出来的假点),只是代表"该窗口内最后已知的值",不保证
 * 逐字节等于原始序列里最早那一条(桶 0 里如果不止一个点,输出的是桶内
 * 最晚那个,不是桶内最早那个——跟其它桶的处理规则完全一致,不搞双重标准)。
 * 用于走势图降噪,不用于任何数值计算——初盘/最新/涨跌方向永远读原始
 * initial/current,不受这里的采样影响。 */
export function downsampleForChart(series: OddsSnapshot[], maxPoints: number): OddsSnapshot[] {
  if (series.length <= maxPoints) return series;
  const t0 = new Date(series[0].observed_at).getTime();
  const t1 = new Date(series[series.length - 1].observed_at).getTime();
  const bucketMs = Math.max(t1 - t0, 1) / maxPoints;
  const out: OddsSnapshot[] = [];
  let bucketIdx = -1;
  let pending: OddsSnapshot | null = null;
  for (const s of series) {
    const t = new Date(s.observed_at).getTime();
    const idx = Math.min(maxPoints - 1, Math.floor((t - t0) / bucketMs));
    if (idx !== bucketIdx) {
      if (pending) out.push(pending);
      bucketIdx = idx;
    }
    pending = s; // 同一桶内不断覆盖,离开桶时留下的就是桶内最后一条
  }
  if (pending) out.push(pending);
  return out;
}

/** 一家公司在一个市场里的走势图(≥2 个点才画)。1x2 三个字段同量纲同轴;
 * ah/ou/corners_ou 有一个盘口线字段(球数门槛),量纲与另外两个水位字段不同,
 * 拆成右侧独立 y 轴,不共用一条刻度尺——否则要么水位被压成一条直线,要么
 * 盘口线的整数跳变淹没水位的小数波动。字段→颜色沿用 1x2 的位置约定
 * (第 1 个=win 色、第 2 个=draw 色、第 3 个=loss 色),盘口线恰好落在中间
 * 天然拿到中性的 draw 色,不需要另开一个语义色。
 *
 * 2026-08-27:盘口线本身是阶梯值(只在 0.25 一档跳),用平滑折线画会显得像
 * 密集毛刺——改成阶梯线(step:'end',"值在下一次变化前保持不变"),水位
 * 字段仍是普通折线,两者视觉语言各自对应各自的真实形状。原始点数超过
 * MAX_CHART_POINTS 时先做 downsampleForChart 分桶精简,不分桶不改数值。 */
export function buildMarketChart(
  snapshots: OddsSnapshot[],
  market: string,
  companyId: string,
  fields: { key: string; label: string; isLine?: boolean }[],
  colors: ChartColors,
): { option: EChartsOption; summary: string } | null {
  const raw = snapshots
    .filter((s) => s.market === market && s.company_id === companyId && flatOddsGroup(s.payload))
    .sort((a, b) => a.observed_at.localeCompare(b.observed_at));
  if (raw.length < 2) return null;
  const series = downsampleForChart(raw, MAX_CHART_POINTS);
  // 北京时间(与下方表格的 LocalTime 一致)——曾用浏览器本地时区,会让同一
  // 组件里图表横轴和表格行显示两套不同的时钟,对中文用户反而更迷惑。
  const times = series.map((s) => formatBeijingZh(s.observed_at) ?? s.observed_at);
  const pick = (key: string) => series.map((s) => flatOddsGroup(s.payload)?.[key] ?? null);
  const company = series[0].company_name || series[0].company_id;
  const first = times[0];
  const last = times[times.length - 1];
  const fieldColors = [colors.win, colors.draw, colors.loss];
  const hasLine = fields.some((f) => f.isLine);
  const option: EChartsOption = {
    grid: { left: 44, right: hasLine ? 44 : 16, top: 30, bottom: 28 },
    legend: {
      data: fields.map((f) => f.label),
      textStyle: { color: colors.axis },
      top: 0,
    },
    xAxis: {
      type: "category",
      data: times,
      axisLabel: { color: colors.axis, fontSize: 10 },
    },
    yAxis: hasLine
      ? [
          {
            type: "value",
            scale: true,
            name: "水位",
            axisLabel: { color: colors.axis },
            splitLine: { lineStyle: { color: colors.grid } },
          },
          {
            type: "value",
            scale: true,
            name: "盘口线",
            axisLabel: { color: colors.axis },
            splitLine: { show: false },
          },
        ]
      : {
          type: "value",
          scale: true,
          axisLabel: { color: colors.axis },
          splitLine: { lineStyle: { color: colors.grid } },
        },
    series: fields.map((f, i) => ({
      name: f.label,
      type: "line",
      step: f.isLine ? "end" : undefined,
      data: pick(f.key),
      color: fieldColors[i] ?? colors.axis,
      yAxisIndex: f.isLine ? 1 : 0,
    })),
  };
  const unitNote = hasLine ? "水位为小数赔率,盘口线为球数门槛" : "欧洲赔率";
  const countNote =
    series.length < raw.length
      ? `共 ${raw.length} 个快照,图上按时间聚合精简为 ${series.length} 个点`
      : `共 ${series.length} 个快照`;
  return {
    option,
    summary: `${company} ${MARKET_ZH[market] ?? market}随观察时间的变化(单位:${unitNote};时间范围:北京时间 ${first} 至 ${last},${countNote})`,
  };
}

/** 一个数字单元格:数值 + 可选的涨跌方向注释(箭头 + 带符号幅度)。
 * 主数值恒用 --ink(中性、权威),不被方向色染——方向色(红涨绿跌,2026-08-26
 * 站长按中国用户习惯拍板,见 CLAUDE.md §11.2)只落在小号注释上,避免整格
 * 变色在密集表格里过于刺眼;红绿是最常见的色盲混淆对,↑/↓ 箭头和 +/− 符号
 * 的双通道冗余因此比之前的配色更重要,不能只靠颜色。 */
function OddsCell({
  value,
  initial,
  showDelta,
  isLine,
}: {
  value: number | null | undefined;
  initial: number | null | undefined;
  showDelta: boolean;
  isLine?: boolean;
}) {
  const { dir, delta } = oddsDelta(initial, value);
  return (
    <span
      className={styles.oCell}
      data-dir={showDelta ? dir : "unknown"}
      data-kind={isLine ? "line" : undefined}
    >
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
 * (initial 缺失)时退化成单行"最新",不假装有初盘。
 * expandable 时整行可点/可键盘操作,展开该公司的完整变化记录抽屉。 */
function CompanyRow({
  market,
  row,
  expandable,
  isOpen,
  onToggle,
}: {
  market: string;
  row: CompanyOddsRow;
  expandable: boolean;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const fields = MARKET_FIELDS[market] ?? [];
  const hasInitial = row.initial != null;
  const lineTag =
    market === "ah" ? ahDirectionZh(row.current?.line ?? row.initial?.line) : null;
  const interactive = expandable
    ? {
        role: "button" as const,
        tabIndex: 0,
        "aria-expanded": isOpen,
        onClick: onToggle,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        },
      }
    : {};
  return (
    <div
      className={styles.coRow}
      data-two={hasInitial}
      data-expandable={expandable || undefined}
      data-open={(expandable && isOpen) || undefined}
      {...interactive}
    >
      <div className={styles.coName}>
        <span className={styles.coLabel}>
          {row.companyLabel}
          {expandable && (
            <span className={styles.chev} aria-hidden>
              {isOpen ? "▾" : "▸"}
            </span>
          )}
        </span>
        {lineTag && <span className={styles.ahTag}>{lineTag}</span>}
        <span className={styles.coTime}>
          <LocalTime iso={row.observedAt} />
        </span>
      </div>
      {hasInitial && (
        <>
          <span className={styles.kind}>初盘</span>
          {fields.map((f) => (
            <span
              key={`i-${f.key}`}
              className={`num ${styles.initNum}`}
              data-kind={f.isLine ? "line" : undefined}
            >
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
          isLine={f.isLine}
        />
      ))}
    </div>
  );
}

/** 点开一家公司后的完整变化记录抽屉(最新在前)。库里每条快照即一次变化,
 * 这里逐条按时间与前一条比着色;超过 HISTORY_LIMIT 条时如实标注总数。 */
function HistoryDrawer({
  market,
  companyLabel,
  snapshots,
}: {
  market: string;
  companyLabel: string;
  snapshots: OddsSnapshot[];
}) {
  const fields = MARKET_FIELDS[market] ?? [];
  const entries = buildCompanyHistory(
    snapshots,
    fields.map((f) => f.key),
  );
  const shown = entries.slice(0, HISTORY_LIMIT);
  return (
    <div className={styles.drawer} role="region" aria-label={`${companyLabel} 完整变化记录`}>
      <div className={styles.drawerHead}>
        {companyLabel} · 完整变化记录 共 {entries.length} 次
        {entries.length > HISTORY_LIMIT && `(显示最近 ${HISTORY_LIMIT} 次)`}
      </div>
      <div className={styles.histGrid}>
        <div className={styles.histHead}>
          <span>时间</span>
          {fields.map((f) => (
            <span key={f.key} data-kind={f.isLine ? "line" : undefined}>
              {f.label}
            </span>
          ))}
        </div>
        {shown.map((e, i) => (
          <div key={`${e.observedAt}|${i}`} className={styles.histRow}>
            <span className={styles.histTime}>
              <LocalTime iso={e.observedAt} />
            </span>
            {fields.map((f) => (
              <span
                key={f.key}
                className={`num ${styles.histCell}`}
                data-dir={e.dirs[f.key]}
                data-kind={f.isLine ? "line" : undefined}
              >
                {renderOddsNum(e.values?.[f.key])}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 公司筛选面板(跨市场共用同一份隐藏集合)。用 &lt;details&gt; 而非自制下拉——
 * 零 JS 状态即可开合、原生键盘可达,与项目里其它折叠面板同一惯用法。 */
function CompanyFilter({
  companies,
  hiddenIds,
  onToggle,
}: {
  companies: { id: string; label: string }[];
  hiddenIds: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (companies.length <= 1) return null; // 只有一家公司时筛选没有意义
  return (
    <details className={styles.filterDetails}>
      <summary className={styles.filterSummary}>
        公司筛选{hiddenIds.size > 0 ? `(已隐藏 ${hiddenIds.size} 家)` : ""}
      </summary>
      <div className={styles.filterPanel}>
        {companies.map((c) => (
          <label key={c.id} className={styles.filterItem}>
            <input
              type="checkbox"
              checked={!hiddenIds.has(c.id)}
              onChange={() => onToggle(c.id)}
            />
            {c.label}
          </label>
        ))}
      </div>
    </details>
  );
}

/** 一个市场的完整块:公司筛选 + 聚合升降摘要 + 表头 + 各公司行(可点开完整
 * 变化记录抽屉)。companyRows 已经是"筛选后可见"的;totalCount 是筛选前
 * 该市场的公司总数,用于摘要里如实声明筛选口径(不能只报筛选后的数字,
 * 那样读者会误以为这场比赛总共只有这几家公司)。 */
function MarketBlock({
  market,
  companyRows,
  totalCount,
  rawByCompany,
  openCompany,
  onToggle,
  phaseNote,
  allCompanies,
  hiddenCompanies,
  onToggleCompanyVisible,
}: {
  market: string;
  companyRows: CompanyOddsRow[];
  totalCount: number;
  rawByCompany: Map<string, OddsSnapshot[]>;
  openCompany: string | null;
  onToggle: (companyId: string) => void;
  phaseNote: string | null;
  allCompanies: { id: string; label: string }[];
  hiddenCompanies: Set<string>;
  onToggleCompanyVisible: (id: string) => void;
}) {
  const fields = MARKET_FIELDS[market] ?? [];
  const primary = ODDS_PRIMARY_FIELD[market];
  const move = primary ? summarizeMarketMovement(companyRows, primary.key) : null;
  const hasMovement = move != null && move.up + move.down + move.flat > 0;
  const filtered = companyRows.length < totalCount;
  return (
    <div className={styles.marketBlock}>
      <CompanyFilter
        companies={allCompanies}
        hiddenIds={hiddenCompanies}
        onToggle={onToggleCompanyVisible}
      />
      <div className={styles.sumBar}>
        <span className={styles.sumTotal}>
          {companyRows.length} 家公司
          {filtered && <span className={styles.sumFiltered}>(共 {totalCount} 家,已筛选)</span>}
        </span>
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
            <span key={f.key} role="columnheader" data-kind={f.isLine ? "line" : undefined}>
              {f.label}
            </span>
          ))}
        </div>
        {companyRows.map((row) => {
          const raw = rawByCompany.get(row.companyId) ?? [];
          const expandable = raw.length > 1; // 只有一条快照没有"变化记录"可看
          const isOpen = openCompany === row.companyId;
          return (
            <Fragment key={`${market}|${row.companyId}`}>
              <CompanyRow
                market={market}
                row={row}
                expandable={expandable}
                isOpen={isOpen}
                onToggle={() => onToggle(row.companyId)}
              />
              {expandable && isOpen && (
                <HistoryDrawer market={market} companyLabel={row.companyLabel} snapshots={raw} />
              )}
            </Fragment>
          );
        })}
      </div>
      <p className={styles.gridHint}>点公司名可展开该公司的完整变化记录</p>
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
  // 展开了完整变化记录抽屉的公司 id(每次只开一家)。切市场时关掉,避免另一个
  // 市场里同 id 公司误留展开态。
  const [openCompany, setOpenCompany] = useState<string | null>(null);
  // 用户勾掉的公司(跨市场共用同一份——公司身份是全局的,不因切 tab 重置)。
  // 默认空集合=全部可见,与"筛选"面板的语义一致(勾选=保留)。
  const [hiddenCompanies, setHiddenCompanies] = useState<Set<string>>(() => new Set());
  const selectMarket = useCallback((m: string) => {
    setPickedMarket(m);
    setOpenCompany(null);
  }, []);
  const toggleCompany = useCallback(
    (companyId: string) => setOpenCompany((cur) => (cur === companyId ? null : companyId)),
    [],
  );
  const toggleCompanyVisible = useCallback((companyId: string) => {
    setHiddenCompanies((cur) => {
      const next = new Set(cur);
      if (next.has(companyId)) next.delete(companyId);
      else next.add(companyId);
      return next;
    });
  }, []);
  // Esc 关闭抽屉(与射门详情面板同一键盘惯例)。只在有展开时挂监听。
  useEffect(() => {
    if (openCompany == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenCompany(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openCompany]);

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

  // 以下全是纯派生值(不是 hook),但要放在这个 hook 之前算好,供下面的
  // useMemo 依赖——resp 不可用时各自安全退化成空集合/null,不需要早退。
  const allSnapshots = resp?.available === true ? resp.snapshots : [];
  const presentMarkets = new Set(allSnapshots.map((s) => s.market));
  const markets = MARKET_ORDER.filter((m) => presentMarkets.has(m));
  // 生效的市场:用户点过且仍存在则用它,否则回落到第一个(市场集合变化时的兜底)。
  const active = pickedMarket && markets.includes(pickedMarket) ? pickedMarket : (markets[0] ?? null);
  const activeSnaps = active ? allSnapshots.filter((s) => s.market === active) : [];
  const rawByCompany = groupByCompany(activeSnaps);
  // 筛选面板只列当前市场出现过的公司,不跨市场汇总——真实数据里同一家公司
  // 在不同市场可能落在不同 company_id 上(如 Bet365 的实时 id 只出现在
  // ah/ou,历史 id 只出现在 1x2),这两个 id 在单个市场内部不会重名共现,
  // 但汇总成一份全局列表就会出现两个都叫"Bet365"却互不联动的勾选项——
  // 用户分不清也勾不明白。按市场切分,同时也不会让用户在这个市场看到一个
  // 跟这个市场毫无关系的公司(如 ah 页面出现只在 1x2 出现过的 Pinnacle)。
  const allCompanies = listCompanies(activeSnaps);

  // 走势图:跟随当前市场 + 当前展开的公司(与下钻抽屉保持一致),不再只锁定
  // 1x2——ah/ou/corners_ou 现在也画(见 buildMarketChart 的双 y 轴设计)。
  const chart = useMemo(() => {
    if (!isFullTimeline || !active) return null;
    const companyId = pickChartCompany(rawByCompany, openCompany, hiddenCompanies);
    if (!companyId) return null;
    const style = getComputedStyle(document.documentElement);
    const readVar = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
    const colors: ChartColors = {
      axis: readVar("--ink-3", "#82969d"),
      grid: readVar("--border", "#203842"),
      win: readVar("--win", "#68c994"),
      draw: readVar("--draw", "#aaa79f"),
      loss: readVar("--loss", "#ef7865"),
    };
    const fields = MARKET_FIELDS[active] ?? [];
    return buildMarketChart(activeSnaps, active, companyId, fields, colors);
    // themeTick 只用来触发重新读取 CSS 变量,不直接参与计算
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, activeSnaps, rawByCompany, openCompany, hiddenCompanies, isFullTimeline, themeTick]);

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

  // active/activeSnaps/rawByCompany/allCompanies 已经在上面(chart 那个
  // useMemo 之前)算好了,这里只需要按公司归并出当前市场的"初盘/最新"行,
  // 再用 hiddenCompanies 筛一遍——totalCount 留筛选前的数字,摘要条要如实
  // 声明"筛选自 N 家",不能让筛选后的数字看起来像这场比赛的全部公司。
  const fieldKeys = (active ? (MARKET_FIELDS[active] ?? []) : []).map((f) => f.key);
  const allCompanyRows = Array.from(rawByCompany.values()).map((list) =>
    summarizeCompanyOdds(list, fieldKeys),
  );
  const visibleCompanyRows = allCompanyRows.filter((r) => !hiddenCompanies.has(r.companyId));

  // 「阶段」在整批快照里如果只有一个真实取值(几乎恒为"赛前"),就并进摘要条
  // 一句话,不再占一列;真出现差异(如混入 in_play)时,退回不展示统一阶段句,
  // 由 CompanyRow 各自的时间承担——不为了好看丢真实差异(§2.1)。
  const allPhases = new Set(allSnapshots.map((s) => s.market_phase));
  const uniformPhase = allPhases.size === 1 ? [...allPhases][0] : null;
  const phaseNote = uniformPhase != null ? (PHASE_ZH[uniformPhase] ?? uniformPhase) : null;

  return (
    <div>
      <MarketTabs markets={markets} active={active ?? ""} onSelect={selectMarket} />

      {/* 走势图跟随当前市场 + 当前展开(或样本最多)的公司,ah/ou/corners_ou
          也画(buildMarketChart 双 y 轴处理盘口线与水位的量纲差异)。 */}
      {chart && (
        <div className={styles.chartWrap}>
          {/* showSummary=false(2026-08-27,同 ShotMapChart 的先例):不再渲染
              图表下方的可见摘要段落——摘要文字并未从可访问性树消失,仍原样
              传给 ariaSummary,落在图表容器 role="img" 的 aria-label 上,
              §11.2"图表要有文字摘要"由此落地,只是不再占版面。 */}
          <EChart option={chart.option} height={220} ariaSummary={chart.summary} showSummary={false} />
        </div>
      )}

      {active && (
        <MarketBlock
          market={active}
          companyRows={visibleCompanyRows}
          totalCount={allCompanyRows.length}
          rawByCompany={rawByCompany}
          openCompany={openCompany}
          onToggle={toggleCompany}
          phaseNote={phaseNote}
          allCompanies={allCompanies}
          hiddenCompanies={hiddenCompanies}
          onToggleCompanyVisible={toggleCompanyVisible}
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
