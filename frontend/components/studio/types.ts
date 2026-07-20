/**
 * Creator Studio 类型:全部从 lib/api-types.ts(OpenAPI 生成,Pydantic 单一真源)
 * 以别名 / Pick / Omit 派生,不再手写字段结构(宪法 §10.3)。
 * 仅当生成类型为宽 dict / 宽 string 时保留窄化类型,并注明后端真源。
 */

import type { components } from "@/lib/api-types";
import type { GetJson, PostJson } from "@/lib/api-v1";

type Schemas = components["schemas"];

export type ScriptSection = Schemas["BundleScriptSection"];
export type SubtitleCue = Schemas["BundleSubtitleCue"];

/**
 * 证据/反向证据/不确定性在编辑器里共用同一列表组件:
 * evidence 项有 side(BundleEvidenceItem),uncertainty 项没有
 * (BundleUncertaintyItem),故 side 以 Partial<Pick> 派生为可选。
 */
export type EvidenceItem = Pick<Schemas["BundleEvidenceItem"], "kind" | "text"> &
  Partial<Pick<Schemas["BundleEvidenceItem"], "side">>;

/** GET /api/v1/studio/matches/{id}/bundle:完整 bundle(含 subtitle_cues)。 */
export type AnalysisBundle = GetJson<"/api/v1/studio/matches/{match_id}/bundle">;

/**
 * 草稿 overrides:只覆盖可编辑文本,冻结的 bundle 本体不动。
 * 契约里 overrides 是宽 dict(UpdateDraftBody.overrides);本类型是其前端窄化,
 * 键名与 backend/api/routes_studio.py 的导出合成逻辑一致。
 */
export interface DraftOverrides {
  title?: string;
  script_sections?: ScriptSection[];
  subtitle_cues?: SubtitleCue[];
  evidence?: EvidenceItem[];
  counter_evidence?: EvidenceItem[];
  uncertainty?: EvidenceItem[];
}

/**
 * 从生成类型窄化:契约里 status 是宽 string;取值由后端
 * DraftStatusBody(pattern="^(draft|reviewed|published)$")与建稿默认 'draft' 保证。
 */
export type DraftStatus = "draft" | "reviewed" | "published";

export type DraftSummary = Omit<Schemas["StudioDraftListItem"], "status"> & {
  status: DraftStatus;
};

/**
 * 从生成类型窄化:契约里 bundle/overrides 是宽 dict(历史草稿形状可能不同);
 * 本仓库当前版本的草稿快照即 StudioBundleDTO 形状,真源 backend/studio/bundle.py。
 */
export type DraftDetail = Omit<
  Schemas["StudioDraftDetailDTO"],
  "status" | "bundle" | "overrides"
> & {
  status: DraftStatus;
  bundle: AnalysisBundle;
  overrides: DraftOverrides;
};

/** 契约里 ExportBody.kind 是宽 string;取值由 routes_studio.EXPORT_KINDS 保证。 */
export type ExportKind =
  | "png_1080x1920"
  | "png_1080x1350"
  | "txt"
  | "json"
  | "srt";

/** side="client"(PNG,前端 DOM 生成)/ side="server"(txt/json/srt)判别联合。 */
export type ExportResult = PostJson<"/api/v1/studio/drafts/{draft_id}/export">;

/** 六段式竖屏场景(顺序即导出顺序)。 */
export const SCENES = [
  { id: "hook", label: "开场钩子" },
  { id: "probability", label: "核心概率" },
  { id: "evidence", label: "支持证据" },
  { id: "risk", label: "反向证据与风险" },
  { id: "charts", label: "数据可视化" },
  { id: "outro", label: "收尾与免责" },
] as const;

export type SceneId = (typeof SCENES)[number]["id"];

export const STATUS_ZH: Record<DraftStatus, string> = {
  draft: "草稿",
  reviewed: "已审阅",
  published: "已发布",
};

export const OUTCOME_ZH: Record<"home" | "draw" | "away", string> = {
  home: "主胜",
  draw: "平局",
  away: "客胜",
};
