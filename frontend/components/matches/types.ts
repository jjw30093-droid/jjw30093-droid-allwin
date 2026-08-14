/**
 * /api/v1 展示层类型:全部从 lib/api-types.ts(OpenAPI 生成,Pydantic 单一真源)
 * 以别名 / Pick / Omit 派生,不再手写字段结构(宪法 §10.3)。
 * 仅当生成类型为宽 dict 时保留窄化的展示类型,并注明后端真源。
 */

import type { components } from "@/lib/api-types";
import type { GetJson } from "@/lib/api-v1";

type Schemas = components["schemas"];

/* ── GET /api/v1/matches/{id}/analysis(公开投影的 analysis_bundle) ── */

export type AnalysisBundle = GetJson<"/api/v1/matches/{match_id}/analysis">;
export type EvidenceItem = Schemas["BundleEvidenceItem"];
export type ScriptSection = Schemas["BundleScriptSection"];

/* ── GET /api/v1/matches/{id}/odds ─────────────────────────── */

/**
 * 从生成类型窄化:OddsSnapshotItem.payload 在契约里是宽 dict。
 * 真实存在两种形状(2026-08-06 审计 B2 实锤,库内 73.5 万行扁平 vs 65 行嵌套):
 * - 扁平(历史回填 CLI):字段→数值,1x2: home/draw/away;ah: home/line/away;
 *   ou: over/line/under —— 与 zh.ts 的 MARKET_FIELDS 键一一对应;
 * - 嵌套(实时轮询):{ initial, latest },每组即上述扁平组,缺失为 null。
 * 渲染统一经 flatOddsGroup() 归一,与后端
 * backend/queries/odds.py::normalize_odds_payload 同规则。
 */
export type OddsGroup = Record<string, number>;

export type OddsSnapshotPayload =
  | OddsGroup
  | { initial: OddsGroup | null; latest: OddsGroup | null };

export type OddsSnapshot = Omit<Schemas["OddsSnapshotItem"], "payload"> & {
  payload: OddsSnapshotPayload;
};

/** 读侧归一:嵌套取 latest(缺则 initial),扁平原样。恒返回扁平组或 null。 */
export function flatOddsGroup(payload: OddsSnapshotPayload): OddsGroup | null {
  if (payload == null || typeof payload !== "object") return null;
  if ("latest" in payload || "initial" in payload) {
    const nested = payload as { initial: OddsGroup | null; latest: OddsGroup | null };
    const group = nested.latest ?? nested.initial;
    return group && typeof group === "object" ? group : null;
  }
  return payload as OddsGroup;
}

type OddsResponseRaw = GetJson<"/api/v1/matches/{match_id}/odds">;

/** available=true / false 判别联合;仅 payload 相对生成类型做上述窄化。 */
export type OddsResponse =
  | (Omit<Extract<OddsResponseRaw, { available: true }>, "snapshots"> & {
      snapshots: OddsSnapshot[];
    })
  | Extract<OddsResponseRaw, { available: false }>;

/* ── GET /api/v1/matches/{id}/cooccurrence ─────────────────── */

export type CooccurrenceItem = Schemas["CooccurrenceItem"];
/** 判别联合:report:deep 含 items 明细;匿名/free 只有计数(items 显式 null)。 */
export type CooccurrenceResponse = GetJson<"/api/v1/matches/{match_id}/cooccurrence">;

/* ── GET /api/v1/model/metrics ─────────────────────────────── */

export type ModelMetricsResponse = GetJson<"/api/v1/model/metrics">;

/* ── GET /api/v1/products ──────────────────────────────────── */

export type ProductsResponse = GetJson<"/api/v1/products">;
export type PlanInfo = Schemas["PlanDTO"];
