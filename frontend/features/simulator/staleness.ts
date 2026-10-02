// 参数新鲜度(纯逻辑,无 node 依赖):服务端 page.tsx 与客户端 MemberSimulator 共用。
import type { SimParams } from "./types";

const STALE_HOURS = 48;

export function isStale(params: SimParams, now = Date.now()): boolean {
  const t = Date.parse(params.meta.generated_at);
  return Number.isFinite(t) && now - t > STALE_HOURS * 3600 * 1000;
}
