// 前端只读 serving API,不直连 DB(CLAUDE.md §2)。基址统一来自 lib/api-base.ts
// (单一真源,宪法 §10.3),本文件不再自行解析 env。
// 本文件的 fetcher 只在 Server Component(旧 /league 页面)里调用,因此用服务端
// 基址(INTERNAL_API_BASE > NEXT_PUBLIC_API_BASE > 127.0.0.1:8000)。
//
// 类型全部从生成的 lib/api-types.ts 派生(Pydantic 单一真源,backend/api/schemas.py
// 的 Legacy* DTO;npm run gen:api 重新生成),不再手写与 API 响应重复的 interface
// (CLAUDE.md §10.3)。
import { serverGet, type LeagueInfo } from "./api-v1";

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

// 说明:排名/赛程/球队榜/球员榜四个页面已迁移到 /api/v1/leagues/{id}/*
// (standings / fixtures / team-stats / players,见 lib/api-v1.ts 与
// components/league/*)。legacy /api/league/{id}/overview 与 /matches 的
// fetcher 已随之删除;后端兼容层端点本身仍保留(deprecated,不再扩展)。
//
// 2026-08-25:WDL 模型与正式预测登记簿已整体废弃(胜率改由 bet365 赔率
// 直接派生),legacy /api/league/{id}/wdl-predictions 端点与本文件对应的
// fetchWdlPredictions/WdlPredictionsResponse 等类型已随之删除。
