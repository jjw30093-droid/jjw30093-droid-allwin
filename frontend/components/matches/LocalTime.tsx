/**
 * UTC 时间戳 → 北京时间(UTC+8)展示。中文足球用户默认按北京时间找比赛
 * (CLAUDE.md §11.2:面向中文用户默认北京时间展示,数据库统一 UTC)。
 *
 * 北京时间是固定 UTC+8、无夏令时的偏移量,服务端与客户端算出的结果恒等——
 * 不像浏览器本地时区那样因客户端环境而异,不需要区分 SSR/hydration 快照,
 * 不需要 useSyncExternalStore 那一套水合后再换算的机制。
 *
 * 用于比赛/预测/赔率/时间共现等赛程语境下的时间戳(kickoff、数据更新、
 * 生成时间、观测时间等)。账户/研究类时间戳(订阅到期、模型评估记录)
 * 用的是 components/trust/LocalTime.tsx,按用户本地时区展示——不是同一
 * 语境,不强求统一。
 */

import { formatBeijingDateTime } from "./zh";

export function LocalTime({
  iso,
  fallback = "—",
}: {
  iso?: string | null;
  fallback?: string;
}) {
  if (!iso) return <>{fallback}</>;
  const beijing = formatBeijingDateTime(iso);
  if (!beijing) return <>{fallback}</>;
  return <time dateTime={iso}>{beijing}</time>;
}
