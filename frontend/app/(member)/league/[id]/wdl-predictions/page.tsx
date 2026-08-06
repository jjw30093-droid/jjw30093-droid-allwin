import Link from "next/link";
import { fetchLeagueNameZh, fetchWdlPredictions, type WdlMatch } from "@/lib/api";
import { buildMatchHref } from "@/lib/match-links";
import { ApiError } from "@/lib/api-v1";
import { LeagueNav } from "@/components/LeagueNav";
import styles from "./wdl-predictions.module.css";

// 三种 JSON 形状物理上互斥(见 lib/api.ts 顶部说明),ProbDistribution/LockedHook
// 各自只接受narrow 后的那一支——TypeScript 据此在编译期就禁止越界读取字段,
// 不依赖调用方自觉只在正确分支下调用。
type WdlLiveFull = Extract<WdlMatch, { availability: "live"; locked: false }>;
type WdlLiveLocked = Extract<WdlMatch, { availability: "live"; locked: true }>;

const TENDENCY_ZH: Record<string, string> = {
  home: "主队",
  draw: "平局",
  away: "客队",
};

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function formatDateZh(dateStr: string | null): string {
  if (!dateStr) return "";
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;
  const [, m, d] = parts;
  return `${parseInt(m, 10)}月${parseInt(d, 10)}日`;
}

// 已付费 + 在 7 天有效期内(availability='live'):呈现"概率分布"
// (主胜 44% / 平局 26% / 客胜 30%),不是"预测胜负"。p_home/p_draw/p_away
// 只在这个分支里被读取——未付费、或距开赛 >7 天的 JSON 形状物理上根本不带
// 这几个字段(LegacyWdlLiveFullMatch 专属),prop 类型已收窄,不接受其他两支。
function ProbDistribution({ m, compact }: { m: WdlLiveFull; compact?: boolean }) {
  const pHome = m.p_home!;
  const pDraw = m.p_draw!;
  const pAway = m.p_away!;
  return (
    <div className={compact ? styles.compact : undefined}>
      <div className={styles.probBar}>
        <span className={styles.segHome} style={{ width: `${pHome * 100}%` }} />
        <span className={styles.segDraw} style={{ width: `${pDraw * 100}%` }} />
        <span className={styles.segAway} style={{ width: `${pAway * 100}%` }} />
      </div>
      <div className={styles.probLabels}>
        <span className={styles.probLabel}>
          <i className={styles.dotHome} />主胜 <span className={styles.num}>{pct(pHome)}</span>
        </span>
        <span className={styles.probLabel}>
          <i className={styles.dotDraw} />平局 <span className={styles.num}>{pct(pDraw)}</span>
        </span>
        <span className={styles.probLabel}>
          <i className={styles.dotAway} />客胜 <span className={styles.num}>{pct(pAway)}</span>
        </span>
      </div>
    </div>
  );
}

// 已在 7 天有效期内(availability='live')但未付费:只有 tendency(倾向词)
// 这一个钩子 + 锁定文案。这个分支的 prop 类型(LegacyWdlLiveLockedMatch 专属)
// 物理上不含 p_home/p_draw/p_away——数据来源是 API 响应本身没下发,不是拿到
// 之后用 CSS/JS 遮起来。
function LockedHook({ m, compact }: { m: WdlLiveLocked; compact?: boolean }) {
  return (
    <div className={`${styles.lockedRow} ${compact ? styles.compact : ""}`}>
      <span className={styles.tendencyHook}>
        模型看好: <span className={styles.num}>{TENDENCY_ZH[m.tendency ?? ""] ?? "暂无"}</span>
      </span>
      <span className={styles.lockCta}>🔒 订阅解锁精确概率</span>
    </div>
  );
}

// 距开赛 >7 天(availability='upcoming'):时候还没到,跟付费与否无关——
// 文案/样式都要和"付费锁"明显不同,不能让用户以为是被锁付费墙挡住了。
function UpcomingNote() {
  return <span className={styles.upcomingNote}>临近开赛更新预测</span>;
}

function MatchCard({ m }: { m: WdlMatch }) {
  return (
    <div className={styles.card}>
      <div className={styles.teamsRow}>
        {/* match_id 与 /matches/[matchId] 同为 FotMob id 空间
            (gold_wdl_predictions 760/760 可 join dim_match,2026-08-06 核实);
            对阵可点进详情(审计 B5) */}
        <Link
          href={buildMatchHref(m.match_id)}
          className={`${styles.teams} ${styles.teamsLink}`}
        >
          <span className={styles.teamName}>{m.home_team_name_zh ?? m.home_team_id}</span>
          <span className={styles.vs}>vs</span>
          <span className={styles.teamName}>{m.away_team_name_zh ?? m.away_team_id}</span>
        </Link>
        <span className={styles.meta}>{m.date}</span>
      </div>
      {m.availability === "live" && m.confidence === "low" && (
        <span className={styles.lowConfidence}>样本有限(赛季初/升班马)</span>
      )}
      {m.availability === "upcoming" ? (
        <div className={styles.upcomingRow}>
          <UpcomingNote />
        </div>
      ) : m.locked ? (
        <LockedHook m={m} />
      ) : (
        <ProbDistribution m={m} />
      )}
    </div>
  );
}

// 同一轮里理论上可能同时存在 live 和 upcoming(比如某场因故提前判定,
// 或本次临时测试改动单场日期造成的混合态),三态判断和列表的 MatchCard
// 保持一致,不能假设"轮次里要么全 live 要么全 upcoming"。
function TopBarMatchCard({ m }: { m: WdlMatch }) {
  return (
    <div className={styles.topBarCard}>
      <div className={styles.topBarTeams}>
        <span className={styles.topBarTeamName}>{m.home_team_name_zh ?? m.home_team_id}</span>
        <span className={styles.vs}>vs</span>
        <span className={styles.topBarTeamName}>{m.away_team_name_zh ?? m.away_team_id}</span>
      </div>
      {m.availability === "upcoming" ? (
        <UpcomingNote />
      ) : m.locked ? (
        <LockedHook m={m} compact />
      ) : (
        <ProbDistribution m={m} compact />
      )}
    </div>
  );
}

// Opta 式顶部横条:取"下一未踢轮次"(数据本身按日期排序,第一场所在的轮次
// 就是下一轮,不用额外发请求)。该轮里只要有场次进入 7 天有效期就展示
// 概率卡横滑条;否则展示占位卡,不透出任何概率/倾向。
function TopBar({ season, nextRoundMatches }: { season: string; nextRoundMatches: WdlMatch[] }) {
  if (nextRoundMatches.length === 0) return null;
  const nextRound = nextRoundMatches[0].round;
  const anyLive = nextRoundMatches.some((m) => m.availability === "live");

  return (
    <section className={styles.topBar}>
      <div className={styles.topBarHeading}>
        下一轮{nextRound ? ` · 第 ${nextRound} 轮` : ""}
      </div>
      {anyLive ? (
        <div className={styles.topBarScroll}>
          {nextRoundMatches.map((m) => (
            <TopBarMatchCard key={m.match_id} m={m} />
          ))}
        </div>
      ) : (
        <div className={styles.topBarPlaceholder}>
          新赛季 {season} · 首轮 {formatDateZh(nextRoundMatches[0].date)}开幕 · 临近开赛更新预测
        </div>
      )}
    </section>
  );
}

export default async function WdlPredictionsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ season?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;

  let data;
  try {
    data = await fetchWdlPredictions(id, sp.season);
  } catch (err) {
    // invalid_season / no_wdl_predictions_for_league:该赛季真的没有预测
    // 数据,不是服务故障——不能用"数据暂时无法加载"这句谎报成外部服务不可用
    // (CLAUDE.md §2.2)。切换器(排名/赛程等页)会让历史赛季一键可达,这个
    // 分支正是切换器落到概率卡 tab 时最常触发的诚实空状态。
    if (
      err instanceof ApiError &&
      (err.code === "invalid_season" || err.code === "no_wdl_predictions_for_league")
    ) {
      return (
        <main className={styles.page}>
          <LeagueNav leagueId={id} active="wdl-predictions" season={sp.season} />
          <div className={styles.emptyBox}>
            <div className={styles.emptyTitle}>该赛季暂无模型预测</div>
            <p>
              模型预测目前只覆盖部分赛季。
              <Link href={`/league/${id}/wdl-predictions`} className={styles.emptyLink}>
                查看默认赛季
              </Link>
            </p>
          </div>
        </main>
      );
    }
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="wdl-predictions" season={sp.season} />
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>
              该联赛数据尚未同步，或数据服务暂时不可用。请稍后再试。
          </p>
        </div>
      </main>
    );
  }

  // 轮次按组内最早比赛日期排序,不按轮次号——真实改期轮次必须出现在它
  // 实际发生的时间位置(同 components/league/FixtureRounds.tsx 的修复;
  // 这个legacy 端点的 WdlMatch 只有 date,没有精确 kickoff,day 粒度已是
  // 这里能拿到的最细时间信号)。
  const earliestDateByRound = new Map<string, string>();
  for (const m of data.matches) {
    if (m.round === null || m.date === null) continue;
    const prev = earliestDateByRound.get(m.round);
    if (!prev || m.date < prev) earliestDateByRound.set(m.round, m.date);
  }
  // 极端情况:某轮所有比赛的 date 都是 null——退回原有轮次号出现顺序,
  // 保证轮次不会因为完全没有可比较的时间键而从列表里消失。
  for (const m of data.matches) {
    if (m.round !== null && !earliestDateByRound.has(m.round)) {
      earliestDateByRound.set(m.round, "");
    }
  }
  const rounds = Array.from(earliestDateByRound.keys()).sort((a, b) =>
    earliestDateByRound.get(a)!.localeCompare(earliestDateByRound.get(b)!)
  );

  // 数据按 Date 排序,第一场所在的轮次就是"下一未踢轮次"。
  const nextRound = data.matches[0]?.round ?? null;
  const nextRoundMatches =
    nextRound !== null ? data.matches.filter((m) => m.round === nextRound) : [];
  const leagueNameZh = await fetchLeagueNameZh(id);

  return (
    <main className={styles.page}>
      {/* 导航只带用户显式选择(sp.season),不是本页解析出的 data.season——
          否则会把这个 tab 的默认值钉死成其它 tab 的显式选择。 */}
      <LeagueNav leagueId={id} active="wdl-predictions" season={sp.season} />
      <div className={styles.header}>
        <h1 className={styles.title}>{leagueNameZh} · WDL 概率卡</h1>
        <span className={styles.seasonChip}>{data.season}</span>
      </div>
      <p className={styles.membershipNote}>
        此页为匿名公开视图(免费层)。完整三项概率需 Pro 会员并登录后查看,详见
        「会员」页;本页展示不构成投注建议。
      </p>

      <TopBar season={data.season} nextRoundMatches={nextRoundMatches} />

      {rounds.map((round) => (
        <div key={round}>
          <div className={styles.roundHeading}>第 {round} 轮</div>
          <div className={styles.grid}>
            {data.matches
              .filter((m) => m.round === round)
              .map((m) => (
                <MatchCard key={m.match_id} m={m} />
              ))}
          </div>
        </div>
      ))}
    </main>
  );
}
