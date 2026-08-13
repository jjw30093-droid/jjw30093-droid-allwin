"use client";

/**
 * 会员联赛数据客户端加载器。
 *
 * 会话 cookie Path=/api/v1,只有浏览器请求能携带(宪法 §10.2:公共 HTML 不因
 * 登录态变化;会员数据由浏览器带 credentials 调私有 API)。服务端匿名取数被
 * 联赛门禁挡下(401/403)时,页面渲染本组件:
 * - 浏览器重试同一端点:已开通会员 → 直接渲染与 SSR 完全相同的展示组件;
 * - 401(未登录)→ 登录 + 会员方案引导;
 * - 403(已登录无权益)→ 会员方案引导;
 * - 404 → 联赛不存在/未同步的诚实说明;
 * - 其他错误 → 可重试的错误态。
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
  | { phase: "gate"; status: 401 | 403 }
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
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setState({ phase: "gate", status: e.status });
        } else if (e instanceof ApiError && e.status === 404) {
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

  if (state.phase === "gate") {
    // 登录后回到当前联赛页,而不是首页(路由段与 API kind 刻意不同:
    // fixtures 的路由是 "matches",见 lib/league-links.ts 头注释)。
    const routeSection = kind === "fixtures" ? "matches" : kind;
    const nextPath = `/league/${leagueId}/${routeSection}${
      season ? `?season=${encodeURIComponent(season)}` : ""
    }`;
    return (
      <div className={styles.gateBox}>
        <h2 className={styles.gateTitle}>登录后即可免费查看该联赛数据</h2>
        <p className={styles.gateText}>
          {state.status === 401
            ? "免费登录后,即可查看该联赛的排名、赛程与数据榜;登录完成后会返回本页。"
            : "当前账号暂无该联赛权限,请重新登录后重试;若仍受限请联系站长。"}
        </p>
        <div className={styles.gateActions}>
          <a
            className={styles.btnPrimary}
            href={`/login?next=${encodeURIComponent(nextPath)}`}
          >
            登录
          </a>
        </div>
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
