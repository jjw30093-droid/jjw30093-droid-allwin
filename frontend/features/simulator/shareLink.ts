// 分享链接:设定写在查询参数里(可读),本次结果压缩后写在 # 片段里。
// # 片段不会随请求发给服务器;打开链接时在浏览器端解压并原样展示,不写任何数据库。

import { FOCUS_LIST, type Focus, type MatchSetup, type TeamSetup } from "./engine";
import { slotAt } from "./formation";
import type { ResultSnapshot } from "./snapshot";
import type { SimParams } from "./types";

export interface SharedTeamSetup {
  teamId: number;
  formation: string;
  slots: { positionId: number; playerId: string }[];
  focuses: Focus[];
  shortRest: boolean;
}

export interface SharedSetup {
  modelVersion: string;
  paramsDate: string;
  leagueId: number;
  seed: number;
  chaos: boolean;
  fixtureId: number | null;
  home: SharedTeamSetup;
  away: SharedTeamSetup;
}

const FORMATION_RE = /^\d(-\d){2,4}$/;
const PLAYER_RE = /^[A-Za-z0-9_-]{1,32}$/;

// ------------------------------------------------------------------ 查询参数(设定)
export function setupQuery(s: SharedSetup): string {
  const q = new URLSearchParams();
  q.set("m", s.modelVersion);
  q.set("pd", s.paramsDate);
  q.set("lg", String(s.leagueId));
  q.set("s", String(s.seed));
  q.set("c", s.chaos ? "1" : "0");
  if (s.fixtureId != null) q.set("fx", String(s.fixtureId));
  for (const [k, t] of [["h", s.home], ["a", s.away]] as const) {
    q.set(k, String(t.teamId));
    q.set(`${k}f`, t.formation);
    q.set(`${k}x`, t.slots.map((sl) => `${sl.positionId}:${sl.playerId}`).join(","));
    q.set(`${k}k`, t.focuses.join("."));
    q.set(`${k}r`, t.shortRest ? "1" : "0");
  }
  return q.toString();
}

function int(v: string | null): number | null {
  if (v == null || !/^-?\d{1,12}$/.test(v)) return null;
  return Number(v);
}

export function parseSetupQuery(search: string): SharedSetup | null {
  const q = new URLSearchParams(search);
  const team = (k: "h" | "a"): SharedTeamSetup | null => {
    const teamId = int(q.get(k));
    const formation = q.get(`${k}f`) ?? "";
    const raw = (q.get(`${k}x`) ?? "").split(",").filter(Boolean);
    const slots = raw.map((pair) => {
      const [pos, pid] = pair.split(":");
      return { positionId: int(pos) ?? NaN, playerId: pid ?? "" };
    });
    const focuses = (q.get(`${k}k`) ?? "").split(".").filter(Boolean);
    if (teamId == null || !FORMATION_RE.test(formation) || slots.length !== 11) return null;
    if (slots.some((s) => !Number.isFinite(s.positionId) || !PLAYER_RE.test(s.playerId))) return null;
    if (focuses.some((f) => !FOCUS_LIST.includes(f as Focus))) return null;
    return { teamId, formation, slots, focuses: focuses as Focus[], shortRest: q.get(`${k}r`) === "1" };
  };
  const home = team("h");
  const away = team("a");
  const leagueId = int(q.get("lg"));
  const seed = int(q.get("s"));
  if (!home || !away || leagueId == null || seed == null) return null;
  return {
    modelVersion: q.get("m") ?? "",
    paramsDate: q.get("pd") ?? "",
    leagueId,
    seed,
    chaos: q.get("c") === "1",
    fixtureId: int(q.get("fx")),
    home,
    away,
  };
}

/** 只带两队的轻量链接 ?lg=&h=&a=[&fx=](跨联赛点选真实比赛;第二次发版的比赛页入口):两队用各自最近一场首发。 */
export function parseTeamsQuery(search: string): { leagueId: number; home: number; away: number; fixtureId: number | null } | null {
  const q = new URLSearchParams(search);
  const leagueId = int(q.get("lg"));
  const home = int(q.get("h"));
  const away = int(q.get("a"));
  if (leagueId == null || home == null || away == null || home === away) return null;
  return { leagueId, home, away, fixtureId: int(q.get("fx")) };
}

export function sharedSetupOf(snap: ResultSnapshot): SharedSetup {
  const t = (i: 0 | 1): SharedTeamSetup => ({
    teamId: snap.teams[i].teamId,
    formation: snap.teams[i].formation,
    slots: snap.teams[i].lineup.map((l) => ({ positionId: l.positionId, playerId: l.playerId ?? "" })),
    focuses: snap.teams[i].focuses,
    shortRest: snap.teams[i].shortRest,
  });
  return {
    modelVersion: snap.modelVersion,
    paramsDate: snap.paramsDate,
    leagueId: snap.leagueId,
    seed: snap.seed,
    chaos: snap.chaos,
    fixtureId: snap.fixtureId,
    home: t(0),
    away: t(1),
  };
}

/** 链接里的设定 → 排阵编辑器的 TeamSetup(分组查当前参数的 position_map)。 */
export function toTeamSetup(params: SimParams, t: SharedTeamSetup): TeamSetup {
  return {
    teamId: t.teamId,
    formation: t.formation,
    focuses: t.focuses,
    shortRest: t.shortRest,
    slots: t.slots.map((s) => slotAt(params, t.formation, s.positionId, s.playerId)),
  };
}

export function toMatchSetup(params: SimParams, s: SharedSetup): MatchSetup {
  return {
    leagueId: s.leagueId,
    home: toTeamSetup(params, s.home),
    away: toTeamSetup(params, s.away),
    fixtureId: s.fixtureId,
    chaos: s.chaos,
  };
}

// ------------------------------------------------------------------ # 片段(结果)
function toBase64Url(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function pipe(bytes: Uint8Array, stream: CompressionStream | DecompressionStream): Promise<Uint8Array> {
  const out = new Response(new Blob([bytes as BlobPart]).stream().pipeThrough(stream));
  return new Uint8Array(await out.arrayBuffer());
}

const canCompress = () => typeof CompressionStream !== "undefined" && typeof DecompressionStream !== "undefined";

/** "z" + deflate-raw 压缩;浏览器不支持压缩流时退回 "j" + 未压缩 JSON。 */
export async function encodeResult(snap: ResultSnapshot): Promise<string> {
  const json = new TextEncoder().encode(JSON.stringify(snap));
  if (canCompress()) return `z${toBase64Url(await pipe(json, new CompressionStream("deflate-raw")))}`;
  return `j${toBase64Url(json)}`;
}

function isSnapshot(x: unknown): x is ResultSnapshot {
  const s = x as ResultSnapshot;
  return (
    !!s &&
    s.sv === 1 &&
    Array.isArray(s.teams) &&
    s.teams.length === 2 &&
    s.teams.every((t) => Array.isArray(t.lineup) && typeof t.name === "string") &&
    Array.isArray(s.single?.score) &&
    Array.isArray(s.single?.events) &&
    typeof s.many?.pHome === "number"
  );
}

export async function decodeResult(token: string): Promise<ResultSnapshot | null> {
  try {
    const kind = token[0];
    const bytes = fromBase64Url(token.slice(1));
    let json: Uint8Array;
    if (kind === "z") {
      if (!canCompress()) return null;
      json = await pipe(bytes, new DecompressionStream("deflate-raw"));
    } else if (kind === "j") json = bytes;
    else return null;
    const parsed: unknown = JSON.parse(new TextDecoder().decode(json));
    return isSnapshot(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export async function buildShareUrl(base: string, snap: ResultSnapshot): Promise<string> {
  return `${base}?${setupQuery(sharedSetupOf(snap))}#r=${await encodeResult(snap)}`;
}

export function resultToken(hash: string): string | null {
  const h = hash.startsWith("#") ? hash.slice(1) : hash;
  return new URLSearchParams(h).get("r");
}
