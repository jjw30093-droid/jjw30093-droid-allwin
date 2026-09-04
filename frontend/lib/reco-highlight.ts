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

export type HighlightLines = {
  main: string;
  /** 有没有值得强调的徽章(连中 / 达标命中率);不达标的不加强调。 */
  emphasize: boolean;
};

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
    return {
      main: `${board} · 近 ${s.length} 单全中${note}`,
      emphasize: true,
    };
  }

  if (h.kind === "parlay_return" && h.window) {
    const n = h.parlay_slip_count ?? 0;
    const net = h.parlay_net_units ?? 0;
    const sign = net >= 0 ? "+" : "";
    return {
      main: `${board} · ${windowLabel(h.window)} · 串关 ${n} 单 回报 ${sign}${net.toFixed(2)} 单位`,
      emphasize: false,
    };
  }

  if (h.rate && h.window) {
    const r = h.rate;
    const seg = h.segment ? segmentLabel(h.segment) : "";
    // 主体是原始计数「N 单 M 中」,不是裸百分比。
    const parts = [board, windowLabel(h.window)];
    if (seg) parts.push(seg);
    parts.push(`${r.decided_count} 单 ${r.win_count} 中`);
    return {
      main: parts.join(" · "),
      emphasize: h.kind === "rate_qualified",
    };
  }

  return null;
}
