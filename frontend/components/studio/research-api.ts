import { clientFetch, type GetJson, type PostJson } from "@/lib/api-v1";

export type ResearchMetric = { evidence_id: string; label: string; value: number | null; unit: string; side: string; definition_version: string };
export type ResearchPacket = {
  schema_version: "video-evidence.v1" | "video-evidence.v2"; kind?: "prematch" | "postmatch" | "round"; title?: string; snapshot_id: string; cutoff_at: string;
  analysis_mode: "current" | "reconstructed";
  display_names: { home?: string; away?: string };
  match: { home_team: string; away_team: string; home_score: number | null; away_score: number | null };
  metrics: ResearchMetric[];
  claims: { topic_id: string; title: string; limitation: string; evidence_refs: string[] }[];
  event_timeline: { event_id: string | number; period_id: number; minute: number; second: number; xg: number | null }[];
  quality: { warnings?: string[]; notes?: string[] };
  source_capture_sha256: Record<string, string>;
};
export type ResearchDraft = GetJson<"/api/v1/studio/drafts/{draft_id}"> & {
  revision: number; bundle: { research_packet: ResearchPacket };
};
export type ResearchHistory = GetJson<"/api/v1/studio/research/drafts/{draft_id}/history">;
export const listResearch = () => clientFetch<GetJson<"/api/v1/studio/research/drafts">>("/api/v1/studio/research/drafts");
export const importResearch = (snapshot_id: string) => clientFetch<PostJson<"/api/v1/studio/research/import">>("/api/v1/studio/research/import", { method: "POST", body: { snapshot_id } });
export async function readResearch(id: string): Promise<ResearchDraft> {
  const draft = await clientFetch<ResearchDraft>(`/api/v1/studio/drafts/${encodeURIComponent(id)}`);
  if (!Number.isInteger(draft.revision) || !draft.bundle?.research_packet || !["video-evidence.v1", "video-evidence.v2"].includes(draft.bundle.research_packet.schema_version)) {
    throw new Error("这不是支持的研究草稿，请从研究列表打开。");
  }
  return draft;
}
export const readHistory = (id: string) => clientFetch<ResearchHistory>(`/api/v1/studio/research/drafts/${encodeURIComponent(id)}/history`);
export const editResearch = (draft: ResearchDraft, title: string, editorial_notes: string) => clientFetch(`/api/v1/studio/drafts/${encodeURIComponent(draft.id)}`, {
  method: "POST", body: { title, overrides: { editorial_notes }, expected_revision: draft.revision },
});
export const reviewResearch = (draft: ResearchDraft, status: "draft" | "reviewed") => clientFetch(`/api/v1/studio/drafts/${encodeURIComponent(draft.id)}/status`, {
  method: "POST", body: { status, expected_revision: draft.revision },
});
export const exportResearch = (id: string) => clientFetch<PostJson<"/api/v1/studio/drafts/{draft_id}/export">>(`/api/v1/studio/drafts/${encodeURIComponent(id)}/export`, { method: "POST", body: { kind: "json", profile: "research-evidence-v1" } });

export const importRound = (snapshot_id: string) => clientFetch<PostJson<"/api/v1/studio/research/import-round">>("/api/v1/studio/research/import-round", {method:"POST",body:{snapshot_id}});
