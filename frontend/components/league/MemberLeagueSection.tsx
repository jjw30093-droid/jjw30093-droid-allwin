"use client";

/**
 * 联赛数据客户端加载器。
 *
 * 会话 cookie Path=/api/v1,只有浏览器请求能携带,服务端(RSC)读不到——本
 * 组件在服务端匿名取数拿不到数据时接手,浏览器带 Cookie 重新拉一次同一端点。
 * 2026-10-02 登录门禁:非英超联赛对匿名返回 401 → 直接跳登录页(登录后回到
 * 本页);登录用户在这里拿到数据,渲染与 SSR 完全相同的展示组件——各栏目
 * 的展示分支必须与对应 page.tsx 的 SSR 分支同构,否则登录后反而少一块。
 * - 404 → 联赛不存在/未同步的诚实说明;
 * - 其他错误 → 可重试的错误态。
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  clientFetch,
  leagueSectionPath,
  type GetJson,
  type LeagueFixturesResponse,
  type PlayerQuadrantResponse,
  type PlayersResponse,
  type StandingsResponse,
  type TeamStatsResponse,
} from "@/lib/api-v1";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import { isLoginRequired, redirectToLogin } from "@/lib/login-gate";
import { FixtureRounds } from "./FixtureRounds";
import { PlayerBoards } from "./PlayerBoards";
import { PlayerQuadrantChart } from "./PlayerQuadrantChart";
import { RecencyVenueSwitcher } from "./RecencyVenueSwitcher";
import { SeasonProfileCharts } from "./SeasonProfileCharts";
import { SeasonSwitcher } from "./SeasonSwitcher";
import seasonSwitcherStyles from "./SeasonSwitcher.module.css";
import { StandingsTable } from "./StandingsTable";
import { TeamStatsSections } from "./TeamStatsSections";
import { TeamQuadrantChart } from "./TeamQuadrantChart";
import { XgLuckChart } from "./XgLuckChart";
import type { TableTypeKey } from "./TableTypeSwitcher";
import styles from "./MemberLeagueSection.module.css";

type SeasonProfile = GetJson<"/api/v1/leagues/{league_id}/season-profile">;

type MemberKind = "standings" | "fixtures" | "team-stats" | "players" | "season-profile";

type SectionData =
  | { kind: "standings"; body: StandingsResponse }
  | { kind: "fixtures"; body: LeagueFixturesResponse }
  | { kind: "team-stats"; body: TeamStatsResponse }
  | { kind: "players"; body: PlayersResponse; quadrant: PlayerQuadrantResponse | null }
  | { kind: "season-profile"; body: SeasonProfile };

type State =
  | { phase: "loading" }
  | { phase: "data"; data: SectionData }
  | { phase: "notfound" }
  | { phase: "error" };

async function loadSection(
  kind: MemberKind,
  leagueId: string,
  opts: { season?: string; tableType?: TableTypeKey; recency?: number; venue?: string },
): Promise<SectionData> {
  const { season, tableType, recency, venue } = opts;
  switch (kind) {
    case "standings": {
      const qs = new URLSearchParams();
      if (season) qs.set("season", season);
      if (tableType && tableType !== "all") qs.set("table_type", tableType);
      const q = qs.toString();
      const body = await clientFetch<StandingsResponse>(
        `/api/v1/leagues/${leagueId}/standings${q ? `?${q}` : ""}`,
      );
      return { kind, body };
    }
    case "fixtures":
      return { kind, body: await clientFetch<LeagueFixturesResponse>(leagueSectionPath(kind, leagueId, { season })) };
    case "team-stats":
      return {
        kind,
        body: await clientFetch<TeamStatsResponse>(
          leagueSectionPath(kind, leagueId, { season, recency, venue }),
        ),
      };
    case "season-profile":
      return { kind, body: await clientFetch<SeasonProfile>(leagueSectionPath(kind, leagueId, { season })) };
    case "players": {
      const body = await clientFetch<PlayersResponse>(leagueSectionPath(kind, leagueId, { season }));
      // 象限图是补充内容,单独失败不拖垮球员榜(与 players/page.tsx 一致)
      const quadrant = await clientFetch<PlayerQuadrantResponse>(
        leagueSectionPath("player-quadrant", leagueId, { season }),
      ).catch(() => null);
      return { kind, body, quadrant };
    }
  }
}

export function MemberLeagueSection({
  kind,
  leagueId,
  season,
  tableType,
  recency,
  venue,
}: {
  kind: MemberKind;
  leagueId: string;
  season?: string;
  /** 只有 standings 用:总榜/主场/客场/近期/xG 榜 */
  tableType?: TableTypeKey;
  /** 只有 team-stats 用:最近 N 场 / 主客场筛选 */
  recency?: number;
  venue?: "home" | "away" | "all";
}) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    loadSection(kind, leagueId, { season, tableType, recency, venue })
      .then((data) => {
        if (cancelled) return;
        setState({ phase: "data", data });
      })
      .catch((e) => {
        if (cancelled) return;
        if (isLoginRequired(e)) {
          // 保持骨架屏,直接去登录页
          redirectToLogin();
        } else if (e instanceof ApiError && e.status === 404) {
          setState({ phase: "notfound" });
        } else {
          setState({ phase: "error" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [kind, leagueId, season, tableType, recency, venue, attempt]);

  const retry = useCallback(() => {
    setState({ phase: "loading" });   // 事件回调里重置,不在 effect 体内直接 setState
    setAttempt((n) => n + 1);
  }, []);

  if (state.phase === "loading") {
    return (
      <div className={styles.skeleton} aria-label="联赛数据加载中">
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
      </div>
    );
  }

  if (state.phase === "notfound") {
    return (
      <div className={styles.gateBox}>
        <h2 className={styles.gateTitle}>联赛不存在或数据未同步</h2>
        <p className={styles.gateText}>请从联赛目录进入已收录的联赛。</p>
        <div className={styles.gateActions}>
          <a className={styles.btnPrimary} href="/leagues">
            返回联赛目录
          </a>
        </div>
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className={styles.gateBox}>
        <h2 className={styles.gateTitle}>数据暂时无法加载</h2>
        <p className={styles.gateText}>数据服务暂时不可用,请稍后再试。</p>
        <div className={styles.gateActions}>
          <button type="button" className={styles.btnSecondary} onClick={retry}>
            重试
          </button>
        </div>
      </div>
    );
  }

  const { data } = state;
  // 赛季切换器由"谁持有数据谁渲染"(审计 B3):页面层的 {data && <SeasonSwitcher/>}
  // 对 Pro 联赛恒为 null(匿名 SSR 被门禁挡下),此前会员成功取到数据后
  // available_seasons 被本组件直接丢弃,Pro 联赛全站无任何赛季 UI。
  // section 是路由段而非 API kind:fixtures 的路由是 "matches"
  // (见 lib/league-links.ts 头注释,两者刻意不同)。
  const section =
    data.kind === "fixtures" ? "matches" : data.kind === "season-profile" ? "overview" : data.kind;
  const switcher = (
    <SeasonSwitcher
      leagueId={leagueId}
      section={section}
      seasons={data.body.available_seasons ?? []}
      selected={season}
      resolved={data.body.season ?? season}
      tableType={data.kind === "standings" ? tableType : undefined}
    />
  );
  switch (data.kind) {
    case "standings":
      return (
        <>
          {switcher}
          {tableType === "xg" ? (
            <XgLuckChart rows={data.body.rows} />
          ) : (
            <StandingsTable rows={data.body.rows} seasonFinished={data.body.season_finished} leagueId={leagueId} />
          )}
        </>
      );
    case "season-profile":
      return (
        <>
          {switcher}
          <SeasonProfileCharts profile={data.body} />
        </>
      );
    case "fixtures":
      return (
        <>
          {switcher}
          <FixtureRounds
            matches={data.body.matches}
            returnTo={buildLeagueSeasonHref(leagueId, "matches", { season })}
          />
        </>
      );
    case "team-stats":
      // 会员加载路径必须和服务端渲染路径(team-stats/page.tsx)同构
      return (
        <>
          <div className={seasonSwitcherStyles.chipRow}>
            <SeasonSwitcher
              leagueId={leagueId}
              section="team-stats"
              seasons={data.body.available_seasons ?? []}
              selected={season}
              resolved={data.body.season ?? season}
              recency={recency}
              venue={venue}
              inline
            />
            <RecencyVenueSwitcher leagueId={leagueId} season={season} recency={recency} venue={venue} />
          </div>
          {data.body.rows.length === 0 ? (
            <p className={styles.empty}>{data.body.empty_reason ?? "该联赛暂无球队赛季统计数据。"}</p>
          ) : (
            <>
              <TeamQuadrantChart rows={data.body.rows} recency={recency} venue={venue} />
              <TeamStatsSections rows={data.body.rows} boards={data.body.boards ?? []} />
            </>
          )}
        </>
      );
    case "players":
      return (
        <>
          {switcher}
          {data.quadrant && data.quadrant.rows.length > 0 && (
            <PlayerQuadrantChart rows={data.quadrant.rows} />
          )}
          <PlayerBoards boards={data.body.boards} />
        </>
      );
  }
}
