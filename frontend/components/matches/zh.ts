/** 比赛相关页面共用的中文映射与格式化(不含任何请求逻辑)。 */

export const OUTCOME_ZH: Record<"home" | "draw" | "away", string> = {
  home: "主胜",
  draw: "平局",
  away: "客胜",
};

export const STATUS_ZH: Record<string, string> = {
  NotStarted: "未开赛",
  InPlay: "进行中",
  Finish: "已完赛",
};

export const PRED_STATUS_ZH: Record<string, string> = {
  published: "已发布",
  locked: "已锁定",
  retracted: "已撤回",
};

export const MARKET_ZH: Record<string, string> = {
  "1x2": "胜平负(欧赔)",
  ah: "亚洲让球",
  ou: "大小球",
};

/** 各市场 payload 字段 → 中文列名(与 backend/providers/nowgoal.py _FIELD_MAP 对齐) */
export const MARKET_FIELDS: Record<string, { key: string; label: string }[]> = {
  "1x2": [
    { key: "home", label: "主胜" },
    { key: "draw", label: "平局" },
    { key: "away", label: "客胜" },
  ],
  ah: [
    { key: "home", label: "主队" },
    { key: "line", label: "盘口线" },
    { key: "away", label: "客队" },
  ],
  ou: [
    { key: "over", label: "大球" },
    { key: "line", label: "盘口线" },
    { key: "under", label: "小球" },
  ],
};

export const EVENT_TYPE_ZH: Record<string, string> = {
  lineup_change: "阵容变化",
  sideline_change: "伤停名单变化",
};

/** 联赛静态元数据(镜像 backend/queries/leagues.py LEAGUE_META,仅作显示回退) */
export const LEAGUE_ZH: Record<number, string> = {
  47: "英超",
  87: "西甲",
  55: "意甲",
  54: "德甲",
  53: "法甲",
};

export const ENTITLEMENT_ZH: Record<string, string> = {
  "league:epl": "英超联赛数据",
  "league:top5": "五大联赛数据",
  "prediction:top_probability": "模型最高一项概率",
  "prediction:full_wdl": "完整胜平负三项概率",
  "prediction:score_matrix": "比分概率矩阵",
  "report:deep": "深度报告与同期事件明细",
  "odds:summary_delayed": "赔率摘要(延迟约 1 小时)",
  "odds:history_full": "完整赔率时间线",
  "odds:raw": "原始赔率快照",
  "export:basic": "基础导出",
  "export:full": "完整导出",
  "alert:odds": "赔率变化提醒",
};

export function entitlementLabel(code: string): string {
  return ENTITLEMENT_ZH[code] ?? code;
}

export function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** "2026-07-19" → "2026年7月19日"(比赛日只精确到日,UTC 口径,不做时区换算) */
export function formatDateZh(dateStr: string): string {
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;
  const [y, m, d] = parts;
  return `${y}年${parseInt(m, 10)}月${parseInt(d, 10)}日`;
}

export function formatPrice(cents: number, currency: string): string {
  const amount = (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2);
  if (currency === "CNY" || currency === "RMB") return `¥${amount}`;
  return `${amount} ${currency}`;
}

/** ISO UTC 的确定性回退文本(SSR/水合一致;本地时区展示交给 LocalTime) */
export function utcFallback(iso: string): string {
  return `${iso.replace("T", " ").replace(/Z$/, "")} UTC`;
}
