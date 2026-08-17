"use client";

/**
 * 联赛数据客户端加载器。
 *
 * 会话 cookie Path=/api/v1,只有浏览器请求能携带,服务端(RSC)读不到——本
 * 组件在服务端匿名取数返回空结果时接手,浏览器重新拉一次同一端点。
 * 2026-08-16 权限口径修正:standings/fixtures/team-stats/players/
 * season-profile 现在对任何人(含匿名)恒 200,不会再返回 401/403——此前
 * "未登录/无权益"引导卡片这条分支已是死代码,一并移除:
 * - 拿到数据 → 直接渲染与 SSR 完全相同的展示组件;
 * - 404 → 联赛不存在/未同步的诚实说明;
 * - 其他错误(含理论上不应再出现的 401/403)→ 统一归入可重试的错误态。
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  clientFetch,
  leagueSectionPath,
  type LeagueFixturesResponse,
  type LeagueSectionKind,
  type PlayersResponse,
  type StandingsResponse,
  type TeamStatsResponse,
} from "@/lib/api-v1";
import { buildLeagueSeasonHref } from "@/lib/league-links";
import { FixtureRounds } from "./FixtureRounds";
import { PlayerBoards } from "./PlayerBoards";
import { SeasonSwitcher } from "./SeasonSwitcher";
import { StandingsTable } from "./StandingsTable";
import { TeamStatsBoards } from "./TeamStatsBoards";
import { TeamQuadrantChart } from "./TeamQuadrantChart";
import styles from "./MemberLeagueSection.module.css";

type SectionData =
  | { kind: "standings"; body: StandingsResponse }
  | { kind: "fixtures"; body: LeagueFixturesResponse }
  | { kind: "team-stats"; body: TeamStatsResponse }
  | { kind: "players"; body: PlayersResponse };

type State =
  | { phase: "loading" }
  | { phase: "data"; data: SectionData }
  | { phase: "notfound" }
  | { phase: "error" };

export function MemberLeagueSection({
  kind,
  leagueId,
  season,
}: {
  kind: LeagueSectionKind;
  leagueId: string;
  season?: string;
}) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    clientFetch<SectionData["body"]>(leagueSectionPath(kind, leagueId, season))
      .then((body) => {
        if (cancelled) return;
        setState({ phase: "data", data: { kind, body } as SectionData });
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setState({ phase: "notfound" });
        } else {
          setState({ phase: "error" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [kind, leagueId, season, attempt]);

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
  const section = data.kind === "fixtures" ? "matches" : data.kind;
  const switcher = (
    <SeasonSwitcher
      leagueId={leagueId}
      section={section}
      seasons={data.body.available_seasons ?? []}
      selected={season}
      resolved={data.body.season ?? season}
    />
  );
  switch (data.kind) {
    case "standings":
      return (
        <>
          {switcher}
          <StandingsTable rows={data.body.rows} />
        </>
      );
    case "fixtures":
      return (
        <>
          {switcher}
          <FixtureRounds
            matches={data.body.matches}
            returnTo={buildLeagueSeasonHref(leagueId, "matches", season)}
          />
        </>
      );
    case "team-stats":
      return (
        <>
          {switcher}
          {/* 会员加载路径必须和服务端渲染路径同构,否则登录后反而少一张图 */}
          <TeamQuadrantChart rows={data.body.rows} />
          <TeamStatsBoards rows={data.body.rows} />
        </>
      );
    case "players":
      return (
        <>
          {switcher}
          <PlayerBoards boards={data.body.boards} />
        </>
      );
  }
}
