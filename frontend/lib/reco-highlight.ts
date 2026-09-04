/**
 * 首页战绩 banner 的文案组装(2026-09)。
 *
 * 后端 `/api/v1/reco/highlight` 已经把口径挑好了(择优逻辑与背景见
 * backend/queries/reco_highlight.py 的模块头注),这里只负责把它变成中文。
 *
 * **设计约定:百分比永远与原始计数同现。** 主行放计数(「5单5中」),细行才放
 * 百分比且紧跟 `(a/b)`。DTO 把 `hit_rate` 和 `decided_count` 放在同一个对象里
 * 就是为了让"只渲染百分比"很别扭;这里再兜一道,并由
 * frontend/tests/reco-highlight.test.ts 的正则断言守住。
 *
 * 本文件**不带 "use client"**:服务端组件要 import 它(§11.4 事故纪律)。
 */

import { MARKET_ZH } from "@/components/matches/zh";
import type { GetJson } from "@/lib/api-v1";

type HighlightResp = GetJson<"/api/v1/reco/highlight">;
export type BoardHighlight = HighlightResp["boards"][number];

/** 名义窗口的中文说明。 */
function windowLabel(w: NonNullable<BoardHighlight["window"]>): string {
  return w.kind === "days" ? `近 ${w.value} 天` : `最近 ${w.value} 单`;
}

/** 分段的中文说明;market 用 MARKET_ZH 映射,`?? market` 兜底必须保留
 *  (market 是自由文本不锁枚举,新市场不能把内部枚举值露给用户)。 */
function segmentLabel(seg: NonNullable<BoardHighlight["segment"]>): string {
  const market = seg.market ? (MARKET_ZH[seg.market] ?? seg.market) : null;
  switch (seg.kind) {
    case "market":
      return market ?? "";
    case "league":
      return seg.league_name_zh ?? "";
    case "league_market":
      return [seg.league_name_zh, market].filter(Boolean).join(" · ");
    default:
      return "";
  }
}

export type HighlightPart = {
  text: string;
  /** 放大成大号 Oswald 数字。每条文案至多一个(有测试守着)。 */
  big?: boolean;
  /** 次级信息,用 --ink-2 覆盖父级的强调红。 */
  muted?: boolean;
};

export type HighlightLines = {
  /** 整行文案(板块 + 口径 + 计数)。横条 banner 不直接渲染它,但它是
   *  「文案必含原始计数」那条不变量的断言对象,也是无样式场景的兜底。 */
  main: string;
  /** 板块短标签(「精选」/「公推」)。横条把它做成灰色前缀,与彩色的
   *  value 分开——`每日` 两字在一行里出现两次纯属噪音。未知板块标签原样
   *  返回,不硬切前两字(切错比长一点更糟)。 */
  boardShort: string;
  /** 除板块外的口径与计数。横条**不直接渲染它**(渲染的是 parts),但它是
   *  "文案必含原始计数""不得出现裸百分比"两条不变量的断言对象。 */
  value: string;
  /** 横条实际渲染的分段。不变量:parts 拼起来逐字节等于 value——拆分不得
   *  让上面那两条守卫守着一个页面上并不存在的字符串。 */
  parts: HighlightPart[];
  /** 有没有值得强调的徽章(连中 / 达标命中率);不达标的不加强调。 */
  emphasize: boolean;
};

/** 「每日精选」→「精选」。只脱已知前缀,不做盲切。 */
function shortBoard(label: string): string {
  return label.startsWith("每日") ? label.slice(2) : label;
}

/** 由分段派生 value + main,保证三者永不漂移(只有这一个出口构造它们)。 */
function lines(
  board: string,
  parts: HighlightPart[],
  emphasize: boolean,
): HighlightLines {
  const value = parts.map((p) => p.text).join("");
  return { main: `${board} · ${value}`, boardShort: shortBoard(board), value, parts, emphasize };
}

/**
 * 把一个板块的择优结果变成一行文案。返回 null 表示这一行不渲染
 * (板块无已结算样本)。
 *
 * 2026-09 站长要求去掉细行(日期区间/口径说明/CTA 文案)——banner 只留最醒目
 * 的一行。**注意:去掉的是细行里的百分比,不是计数**——主行本来就以
 * 「N 单 M 中」的原始计数为主体,所以精简之后展示的仍然是真实计数而非裸
 * 百分比。这条由本文件的测试守住(见 reco-highlight.test.ts)。
 *
 * 连中若"其间"跳过了走水/作废,仍然会在主行末尾附一小段披露——那种情况下
 * 「近 N 单全中」不加说明会失真。真实生产序列(走水落在连中之外)不触发它。
 */
export function highlightLines(h: BoardHighlight): HighlightLines | null {
  const board = h.board_label_zh;

  if (h.kind === "empty") return null;

  if (h.kind === "streak" && h.streak) {
    const s = h.streak;
    const skipped: string[] = [];
    if (s.skipped_push_count > 0) skipped.push(`${s.skipped_push_count} 单走水`);
    if (s.skipped_void_count > 0) skipped.push(`${s.skipped_void_count} 单作废`);
    const note = skipped.length > 0 ? `（其间 ${skipped.join("、")}不计）` : "";
    // 回报只加在连中分支:rate 分支保持命中率口径,不混。样本与 length
    // 逐单一致(后端 Streak.net_units 已保证),所以「近 N 单全中」和
    // 「回报 +X%」说的是同一批单。
    //
    // **口径 = 累计净利 ÷ 单注**(净单位 × 100%),不是 ROI(净利 ÷ 总投入)。
    // 站长 2026-09-04 明确选定,举的例子是"一单 100 赢 80、再一单赢 80,
    // 累计 160 就是 160%"。与站内既有的「净单位」(app/page.tsx、
    // app/reco/page.tsx、下面 parlay 分支)是同一把尺子,只是换成百分比表达。
    //
    // 已如实告知站长的代价:这个数**随出单量增长,不只随水平增长**——
    // 100 单每单净赚 0.5 是 +5000%,5 单每单净赚 1.19 是 +597%,前者看着好
    // 8 倍但每单其实差一半。它是"累计战绩"不是"效率",不同时期不可比。
    // net_units 缺失/非有限值时**整段回报不渲染**,退回改版前的文案。
    // 这不是防御性洁癖:/reco/highlight 带 s-maxage=300 且服务端 fetch
    // revalidate=300,所以每次部署后最长 5 分钟内,新前端会真实收到不含
    // net_units 的旧缓存响应(本地实测就渲染出了「回报 NaN%」)。
    // DTO 上该字段是必填,但"契约必填"挡不住"缓存里的旧响应"。
    const net = s.net_units;
    const roi =
      typeof net === "number" && Number.isFinite(net) && s.length > 0
        ? [{
            text: ` · 回报 ${net >= 0 ? "+" : ""}${Math.round(net * 100)}%`,
            muted: true,
          }]
        : [];
    return lines(board, [
      { text: "近 " },
      { text: String(s.length), big: true },
      { text: ` 单全中${note}` },
      ...roi,
    ], true);
  }

  if (h.kind === "parlay_return" && h.window) {
    const n = h.parlay_slip_count ?? 0;
    const net = h.parlay_net_units ?? 0;
    const sign = net >= 0 ? "+" : "";
    return lines(board, [
      { text: `${windowLabel(h.window)} · 串关 ${n} 单 回报 ` },
      { text: `${sign}${net.toFixed(2)}`, big: true },
      { text: " 单位" },
    ], false);
  }

  if (h.rate && h.window) {
    const r = h.rate;
    const seg = h.segment ? segmentLabel(h.segment) : "";
    // 主体是原始计数「N 单 M 中」,不是裸百分比。
    const head = [windowLabel(h.window)];
    if (seg) head.push(seg);
    // 头号数字是"中了几单"——放大它,而不是分母。
    return lines(board, [
      { text: `${head.join(" · ")} · ${r.decided_count} 单 ` },
      { text: String(r.win_count), big: true },
      { text: " 中" },
    ], h.kind === "rate_qualified");
  }

  return null;
}
