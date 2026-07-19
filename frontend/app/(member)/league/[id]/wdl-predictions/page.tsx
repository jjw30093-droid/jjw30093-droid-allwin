import { fetchWdlPredictions, type WdlMatch } from "@/lib/api";
import { LeagueNav } from "@/components/LeagueNav";
import styles from "./wdl-predictions.module.css";

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
// 只在这个分支里被读取——未付费、或距开赛 >7 天的 WdlMatch 对象根本不带
// 这几个字段,这个函数只会在 m.locked === false 时被调用。
function ProbDistribution({ m, compact }: { m: WdlMatch; compact?: boolean }) {
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
// 这一个钩子 + 锁定文案。这个分支不引用、也读不到 m.p_home/p_draw/p_away
// ——数据来源是 API 响应本身没下发,不是拿到之后用 CSS/JS 遮起来。
function LockedHook({ m, compact }: { m: WdlMatch; compact?: boolean }) {
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
        <div className={styles.teams}>
          <span className={styles.teamName}>{m.home_team_name_zh ?? m.home_team_id}</span>
          <span className={styles.vs}>vs</span>
          <span className={styles.teamName}>{m.away_team_name_zh ?? m.away_team_id}</span>
        </div>
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
    return (
      <main className={styles.page}>
        <LeagueNav leagueId={id} active="wdl-predictions" season={sp.season} />
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>
            后端 serving API 未响应,请确认 backend/api_server.py 已启动。
            <br />
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
              {err instanceof Error ? err.message : String(err)}
            </span>
          </p>
        </div>
      </main>
    );
  }

  const rounds = Array.from(
    new Set(data.matches.map((m) => m.round).filter((r): r is string => r !== null))
  ).sort((a, b) => Number(a) - Number(b));

  // 数据按 Date 排序,第一场所在的轮次就是"下一未踢轮次"。
  const nextRound = data.matches[0]?.round ?? null;
  const nextRoundMatches =
    nextRound !== null ? data.matches.filter((m) => m.round === nextRound) : [];

  return (
    <main className={styles.page}>
      <LeagueNav leagueId={id} active="wdl-predictions" season={data.season} />
      <div className={styles.header}>
        <h1 className={styles.title}>26/27 英超 · WDL 概率卡</h1>
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
