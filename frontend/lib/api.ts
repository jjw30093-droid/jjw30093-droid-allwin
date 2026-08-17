// 前端只读 serving API,不直连 DB(CLAUDE.md §2)。基址统一来自 lib/api-base.ts
// (单一真源,宪法 §10.3),本文件不再自行解析 env。
// 本文件的 fetcher 只在 Server Component(旧 /league 页面)里调用,因此用服务端
// 基址(INTERNAL_API_BASE > NEXT_PUBLIC_API_BASE > 127.0.0.1:8000)。
//
// 类型全部从生成的 lib/api-types.ts 派生(Pydantic 单一真源,backend/api/schemas.py
// 的 Legacy* DTO;npm run gen:api 重新生成),不再手写与 API 响应重复的 interface
// (CLAUDE.md §10.3)。旧手写类型曾对同一来源列做出不一致的可空性假设(如
// MatchRow.Match_Round 声明为非空,而 wdl-predictions 的同源字段声明可空)——
// 生成类型按 backend/api_server.py 真实返回值精确建模,消除了这一不一致。
import { serverApiBase } from "./api-base";
import {
  ApiError,
  parseErrorBody,
  safeParseJsonError,
  serverGet,
  type GetJson,
  type LeagueInfo,
} from "./api-v1";

/**
 * 旧 /league/[id]/* 页面的联赛中文名标题:唯一真源仍是后端 LEAGUE_META
 * (经 /api/v1/leagues 暴露),不在前端另建一份联赛名单——这些页面此前把标题写死成
 * "英超",瑞典超接入后才暴露(CLAUDE.md §11.1:旧页面须保留且接入新导航/API)。
 * 查不到时退化为 "联赛 {id}",不假装是英超。
 */
export async function fetchLeagueNameZh(leagueId: string): Promise<string> {
  const leagues = await serverGet<LeagueInfo[]>("/api/v1/leagues");
  const found = leagues.find((l) => String(l.league_id) === leagueId);
  return found?.name_zh ?? `联赛 ${leagueId}`;
}

function seasonQuery(season?: string): string {
  return season ? `?season=${encodeURIComponent(season)}` : "";
}

// 说明:排名/赛程/球队榜/球员榜四个页面已迁移到 /api/v1/leagues/{id}/*
// (standings / fixtures / team-stats / players,见 lib/api-v1.ts 与
// components/league/*)。legacy /api/league/{id}/overview 与 /matches 的
// fetcher 已随之删除;后端兼容层端点本身仍保留(deprecated,不再扩展)。

// WDL 概率卡(legacy 端点)。2026-08-16 权限口径修正后,两种 JSON 形状
// 物理上互斥(后端 LegacyWdlUpcomingMatch / LegacyWdlLiveMatch 判别联合,
// 各自 extra=forbid),不再有 locked 字段区分付费/未付费:
//   1. availability='upcoming'(距开赛 >7 天):物理上没有 tendency/confidence/
//      reason/p_home/p_draw/p_away 这些字段——时候没到,谁都看不到(数据就绪
//      状态,不是权限门禁)。
//   2. availability='live':恒带 tendency/confidence/reason,p_home/p_draw/
//      p_away 只要模型算出来就下发(可能为 null,同样是数据就绪问题,不是
//      身份分层)。
// 消费方按 availability 收窄即可访问对应字段——这是编译期强制,不是约定俗成。
export type WdlPredictionsResponse = GetJson<"/api/league/{league_id}/wdl-predictions">;
export type WdlMatch = WdlPredictionsResponse["matches"][number];
export type WdlLiveMatch = Extract<WdlMatch, { availability: "live" }>;
export type Tendency = NonNullable<WdlLiveMatch["tendency"]>;
export type Confidence = NonNullable<WdlLiveMatch["confidence"]>;
export type Availability = WdlMatch["availability"];

export async function fetchWdlPredictions(
  leagueId: string,
  season?: string
): Promise<WdlPredictionsResponse> {
  const url = `${serverApiBase()}/api/league/${leagueId}/wdl-predictions${seasonQuery(season)}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    // legacy 端点与 v1 共用同一套统一错误契约({code, message, details}),
    // 抛类型化 ApiError 而不是裸 Error——调用方(概率卡页)需要按 code
    // 区分"该赛季没有预测数据"(invalid_season)与真实故障,不能把两者都
    // 显示成同一句"数据服务暂时不可用"(那是编造的故障声明)。
    const raw = await safeParseJsonError(res);
    throw new ApiError(res.status, parseErrorBody(res.status, raw));
  }
  return res.json();
}
