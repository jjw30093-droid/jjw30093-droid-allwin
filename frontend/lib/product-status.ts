import type { MatchSummary } from "@/lib/api-v1";

export function syncStateLabel(state: MatchSummary["sync_state"]): string {
  if (state === "FRESH") return "数据已更新";
  if (state === "STALE") return "数据等待刷新";
  if (state === "UNAVAILABLE") return "部分数据暂不可用";
  return "赛前数据";
}
