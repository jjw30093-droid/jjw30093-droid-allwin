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
export type UncertaintyItem = Schemas["BundleUncertaintyItem"];
export type SourceNote = Schemas["BundleSourceNote"];
export type ScriptSection = Schemas["BundleScriptSection"];
export type AnalysisChartSpec = Schemas["BundleChartSpec"];

/* ── GET /api/v1/matches/{id}/odds ─────────────────────────── */

/**
 * 从生成类型窄化:OddsSnapshotItem.payload 在契约里是宽 dict。
 * 真实结构以 backend/providers/nowgoal.py 的 canonical payload 为准:
 * { initial, latest },每组为 字段→欧洲赔率数值(1x2: home/draw/away;
 * ah: home/line/away;ou: over/line/under),缺失组为 null。
 */
export type OddsGroup = Record<string, number>;

export type OddsSnapshot = Omit<Schemas["OddsSnapshotItem"], "payload"> & {
  payload: { initial: OddsGroup | null; latest: OddsGroup | null };
};

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
export type ModelVersionInfo = Schemas["ModelVersionDTO"];
export type OfficialEvaluation = Schemas["OfficialEvaluationDTO"];

/* ── GET /api/v1/products ──────────────────────────────────── */

export type ProductsResponse = GetJson<"/api/v1/products">;
export type PlanInfo = Schemas["PlanDTO"];
export type ProductInfo = Schemas["ProductDTO"];
