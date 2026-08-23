"use client";

import { useMemo, useState } from "react";
import type { EChartsOption, LineSeriesOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { ChartMode } from "@/components/charts/chartMode";
import { SHOT_SITUATION_ZH, SHOT_TYPE_ZH } from "@/components/matches/zh";
import styles from "./ShotMapExplorer.module.css";

type Scope = "same_league" | "all_covered";
type Mode = "created" | "conceded";
type OutcomeFilter = "all" | "on_target" | "goal";
type HalfFilter = "all" | "first" | "second";

/** 与 ShotMapChart 同一份"竞彩用户会主动筛"的情境/部位子集(库里情境有 8
 * 种,身体部位另有"其他部位",这里只列最常筛的几个,保持两个射门图的
 * 筛选口径一致)。 */
const SITUATION_CHIPS = ["FromCorner", "SetPiece", "FreeKick", "FastBreak", "Penalty"];
const BODY_PARTS = ["LeftFoot", "RightFoot", "Header"];

type Shot = {
  shot_id: string;
  match_id: number;
  player_id: string | null;
  team_id: number;
  minute: number | null;
  period: string | null;
  x: number;
  y: number;
  xg: number | null;
  outcome: string | null;
  situation: string | null;
  shot_type: string | null;
  // 2026-08-23 起才采集(见 backend/migrations/core/0005_shotmap_raw_fields.sql),
  // 旧场次/未回填场次恒为 null——区分"未知"和"false",不能当 false 用。
  is_blocked?: boolean | null;
  is_on_target?: boolean | null;
};

type OfficialStat = {
  match_id: number;
  team_id: number;
  shots_on_target: number | null;
  blocked_shots: number | null;
  total_shots: number | null;
};

type ShotMapData = {
  pitch: { length: number; width: number };
  coordinate_note: string;
  window: number;
  teams: Array<{ team_id: number; name: string; side: string }>;
  recent_sets: Record<
    string,
    Record<Scope, { match_ids: number[]; matched_games: number; shot_covered_games: number }>
  >;
  matches: Array<{
    match_id: number;
    date_utc: string;
    home: { team_id: number | null; name: string };
    away: { team_id: number | null; name: string };
  }>;
  shots: Shot[];
  /** fact_team_match_stats 官方口径(ShotsOnTarget/blocked_shots/total_shots),
   * 用于修正逐次射门(fact_shotmap.Outcome)按 Goal+AttemptSaved 算"射正"
   * 会把被后卫封堵的射门也计入的超算问题(实测 26,067 队场:7.75 vs 官方
   * 4.36,只有 6.8% 完全吻合)。可能不是每场都有,前端按覆盖情况诚实降级。 */
  official_stats?: OfficialStat[];
};

/** 给定当前选中的场次与"本队/对手"视角,汇总官方口径射正——单场对拍用
 * fact_team_match_stats,不再从逐次射门的 Outcome 里数 AttemptSaved。 */
export function officialOnTargetSum(
  data: ShotMapData,
  matchIds: number[],
  teamId: number,
  mode: Mode,
): { value: number | null; covered: number; total: number } {
  const byKey = new Map(
    (data.official_stats ?? []).map((s) => [`${s.match_id}:${s.team_id}`, s]),
  );
  const matchById = new Map(data.matches.map((m) => [m.match_id, m]));
  let sum = 0;
  let covered = 0;
  for (const mid of matchIds) {
    const match = matchById.get(mid);
    if (!match) continue;
    const resolvedTeamId =
      mode === "created"
        ? teamId
        : match.home.team_id === teamId
          ? match.away.team_id
          : match.home.team_id;
    if (resolvedTeamId == null) continue;
    const stat = byKey.get(`${mid}:${resolvedTeamId}`);
    if (stat?.shots_on_target == null) continue;
    sum += stat.shots_on_target;
    covered += 1;
  }
  return { value: covered > 0 ? sum : null, covered, total: matchIds.length };
}

/** 官方口径射正来自 fact_team_match_stats 的整场球队统计,库里**没有**半场/
 * 情境/身体部位维度,构造上不可能响应这三个筛选。因此只要用户动了任何一个
 * 子筛选,就必须退回逐次射门口径(summarizeShotRows),否则统计格的数字会和
 * 场上画出的点自相矛盾(如"上半场射门31次却射正21次"这种语义上不可能的组合)。
 * ⚠️ 新增任何射门子筛选,必须同时加进这个判定,否则会重现 2026-08 的这个缺陷。 */
export function officialOnTargetApplies(
  outcome: OutcomeFilter,
  half: HalfFilter,
  situations: string[],
  bodyPart: string | null,
): boolean {
  return outcome === "all" && half === "all" && situations.length === 0 && bodyPart === null;
}

export type ShotSummary = {
  shots: number;
  onTarget: number;
  goals: number;
  xg: number | null;
};

const GOAL_LINE_X = 105;
const HALF_WAY_X = 52.5;
const PITCH_WIDTH = 68;

/** 默认展示"同联赛"口径;该球队同联赛内没有历史射门时(如刚升级的球队,
 * 只有原联赛的数据),自动退回"全部赛事"口径——避免默认视图对着一支
 * 有真实覆盖率的球队显示"0/0 场",让用户误以为这场比赛没有可视化内容。 */
function preferredScopeFor(data: ShotMapData, teamId: number): Scope {
  const sets = data.recent_sets[String(teamId)];
  if (sets?.same_league?.matched_games) return "same_league";
  if (sets?.all_covered?.matched_games) return "all_covered";
  return "same_league";
}

/** 射正判定,与 ShotMapChart.tsx 的 isOnTarget 同一套口径:is_blocked 已知
 * 时精确排除被封堵的球(is_on_target 本身不排除),未知时退回旧的
 * Outcome 口径(混入被封堵球)。 */
function isPreciseOnTarget(shot: Shot): boolean {
  if (shot.outcome === "Goal") return true;
  if (shot.outcome !== "AttemptSaved") return false;
  return shot.is_blocked == null ? true : !shot.is_blocked;
}

export function filterShotRows(
  data: ShotMapData,
  teamId: number,
  scope: Scope,
  mode: Mode,
  outcome: OutcomeFilter,
  matchId?: number,
  situations: string[] = [],
  bodyPart: string | null = null,
  half: HalfFilter = "all",
): Shot[] {
  const matchIds = new Set(data.recent_sets[String(teamId)]?.[scope]?.match_ids ?? []);
  return data.shots.filter((shot) => {
    // 点球大战不属于比赛 xG/射门统计,与 ShotMapChart.tsx(单场射门图)
    // 的排除口径对齐——此前只有 ShotMapChart 排除,这里不排除,同一个站
    // 的两张射门图对"什么算一脚射门"口径不一致(2026-08-23 对照修复)。
    if (shot.period === "PenaltyShootout") return false;
    if (!matchIds.has(shot.match_id)) return false;
    if (matchId !== undefined && shot.match_id !== matchId) return false;
    if (mode === "created" ? shot.team_id !== teamId : shot.team_id === teamId) return false;
    if (outcome === "goal" && shot.outcome !== "Goal") return false;
    if (outcome === "on_target" && !isPreciseOnTarget(shot)) return false;
    if (situations.length > 0 && !situations.includes(shot.situation ?? "")) return false;
    if (bodyPart && shot.shot_type !== bodyPart) return false;
    if (half === "first" && shot.period !== "FirstHalf") return false;
    if (half === "second" && shot.period !== "SecondHalf") return false;
    return true;
  });
}

export function summarizeShotRows(rows: Shot[]): ShotSummary {
  const xgValues = rows
    .map((shot) => shot.xg)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  return {
    shots: rows.length,
    onTarget: rows.filter(isPreciseOnTarget).length,
    goals: rows.filter((shot) => shot.outcome === "Goal").length,
    xg:
      xgValues.length > 0
        ? Number(xgValues.reduce((total, value) => total + value, 0).toFixed(3))
        : null,
  };
}

function isShotMapData(value: Record<string, unknown>): value is Record<string, unknown> & ShotMapData {
  return (
    Array.isArray(value.teams) &&
    Array.isArray(value.shots) &&
    Array.isArray(value.matches) &&
    typeof value.recent_sets === "object" &&
    value.recent_sets !== null
  );
}

function pitchLine(data: Array<[number, number]>): LineSeriesOption {
  return {
    type: "line",
    data,
    symbol: "none",
    silent: true,
    animation: false,
    lineStyle: {
      color: "rgba(226, 247, 241, 0.76)",
      width: 1.25,
    },
    emphasis: { disabled: true },
    tooltip: { show: false },
    z: 1,
  };
}

function arcPoints(
  centerY: number,
  centerX: number,
  radius: number,
  startDegrees: number,
  endDegrees: number,
): Array<[number, number]> {
  const points: Array<[number, number]> = [];
  for (let degrees = startDegrees; degrees <= endDegrees; degrees += 4) {
    const radians = (degrees * Math.PI) / 180;
    points.push([
      centerY + radius * Math.cos(radians),
      centerX + radius * Math.sin(radians),
    ]);
  }
  return points;
}

// 真实枚举只有 4 个值(Goal/AttemptSaved/Miss/Post,库内实测无 Blocked——
// FotMob 把"被后卫封堵"也计入 AttemptSaved,见下),与
// frontend/components/matches/zh.ts 的 SHOT_OUTCOME_ZH 保持同一套措辞。
export function outcomeLabel(outcome: string | null, isBlocked?: boolean | null): string {
  if (outcome === "Goal") return "进球";
  if (outcome === "AttemptSaved") {
    // is_blocked 已知时(该场已回填,2026-08-23 起才采集,见
    // backend/migrations/core/0005_shotmap_raw_fields.sql)能精确区分
    // "门将扑出"和"被后卫封堵";未知时不能标"射正被扑",那是编出来的
    // 精确度,退回两者皆可能的措辞。
    if (isBlocked != null) return isBlocked ? "被封堵" : "被扑出";
    return "被扑/被挡";
  }
  if (outcome === "Post") return "击中门框";
  if (outcome === "Miss") return "偏出";
  return "结果未标注";
}

function matchOpponent(
  match: ShotMapData["matches"][number] | undefined,
  teamId: number,
): string {
  if (!match) return "对手";
  return match.home.team_id === teamId ? match.away.name : match.home.name;
}

// date_utc 是比赛自然日字符串"YYYY-MM-DD"(dim_match.Date,无精确时刻),
// 直接切片重排,不经 Date/时区转换——没有时刻就没有时区可言,经 Date 解析
// 反而会引入不必要的时区依赖(时区展示纪律见 tests/timezone-discipline.test.ts)。
function shortDate(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!m) return "日期待同步";
  return `${m[2]}/${m[3]}`;
}

export function ShotMapExplorer({
  raw,
  height = 360,
  mode: renderMode = "interactive",
}: {
  raw: Record<string, unknown>;
  height?: number;
  /**
   * export 模式用于 Studio 卡片:隐藏全部筛选控件 —— 截进 PNG 的按钮是死的,
   * 只会占掉卡面并让读者以为可以点。见 components/charts/chartMode.ts。
   */
  mode?: ChartMode;
}) {
  if (!isShotMapData(raw) || raw.teams.length < 2) return null;
  return <ShotMapExplorerReady data={raw} height={height} renderMode={renderMode} />;
}

function ShotMapExplorerReady({
  data,
  height,
  renderMode,
}: {
  data: ShotMapData;
  height: number;
  renderMode: ChartMode;
}) {
  const isExport = renderMode === "export";
  const [teamId, setTeamId] = useState(data.teams[0].team_id);
  const [scope, setScope] = useState<Scope>(() => preferredScopeFor(data, data.teams[0].team_id));
  const [mode, setMode] = useState<Mode>("created");
  const [outcome, setOutcome] = useState<OutcomeFilter>("all");
  const [half, setHalf] = useState<HalfFilter>("all");
  const [situations, setSituations] = useState<string[]>([]);
  const [bodyPart, setBodyPart] = useState<string | null>(null);
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);
  const team = data.teams.find((item) => item.team_id === teamId) ?? data.teams[0];
  const coverage = data.recent_sets[String(teamId)]?.[scope];
  const matchById = useMemo(
    () => new Map(data.matches.map((match) => [match.match_id, match])),
    [data.matches],
  );
  const coveredMatches = (coverage?.match_ids ?? [])
    .map((matchId) => matchById.get(matchId))
    .filter((match): match is ShotMapData["matches"][number] => match !== undefined);
  const rows = useMemo(
    () =>
      filterShotRows(
        data,
        teamId,
        scope,
        mode,
        outcome,
        selectedMatchId ?? undefined,
        situations,
        bodyPart,
        half,
      ),
    [data, teamId, scope, mode, outcome, selectedMatchId, situations, bodyPart, half],
  );
  const summary = useMemo(() => summarizeShotRows(rows), [rows]);

  const official = useMemo(() => {
    const matchIds = selectedMatchId != null ? [selectedMatchId] : (coverage?.match_ids ?? []);
    return officialOnTargetSum(data, matchIds, teamId, mode);
  }, [data, teamId, mode, selectedMatchId, coverage]);
  // 官方口径射正只在没有任何射门子筛选(结果/半场/情境/身体部位)生效时替换
  // 显示值——用户主动筛这些维度时,统计格的数字要和场上实际画出的点一致,
  // 否则会出现"格子写 23,场上却画着 41 个点"这种自相矛盾,此时退回逐次射门口径。
  // 判定逻辑见 officialOnTargetApplies() 的完整警告注释。
  const noSubFilter = officialOnTargetApplies(outcome, half, situations, bodyPart);
  const onTargetDisplay =
    noSubFilter && official.value != null ? official.value : summary.onTarget;
  const onTargetIsOfficial = noSubFilter && official.value != null;

  const option = useMemo<EChartsOption>(
    () => ({
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 12, right: 12, top: 18, bottom: 14, containLabel: false },
      xAxis: {
        type: "value",
        min: -2,
        max: PITCH_WIDTH + 2,
        show: false,
      },
      yAxis: {
        type: "value",
        min: HALF_WAY_X - 1,
        max: GOAL_LINE_X + 4,
        show: false,
      },
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: "#071f25",
        borderColor: "rgba(92, 224, 203, 0.5)",
        textStyle: { color: "#f5fbfa", fontSize: 12 },
        formatter: (params) => {
          const item = (params as { data?: { shot?: Shot } }).data?.shot;
          if (!item) return "";
          const match = matchById.get(item.match_id);
          return [
            `${shortDate(match?.date_utc ?? "")} · 对 ${matchOpponent(match, teamId)}`,
            `${item.minute ?? "?"}′ · ${outcomeLabel(item.outcome, item.is_blocked)}`,
            `xG ${item.xg == null ? "未提供" : item.xg.toFixed(2)}`,
          ].join("<br/>");
        },
      },
      series: [
        pitchLine([
          [0, HALF_WAY_X],
          [0, GOAL_LINE_X],
          [PITCH_WIDTH, GOAL_LINE_X],
          [PITCH_WIDTH, HALF_WAY_X],
          [0, HALF_WAY_X],
        ]),
        pitchLine([
          [13.84, GOAL_LINE_X],
          [13.84, 88.5],
          [54.16, 88.5],
          [54.16, GOAL_LINE_X],
        ]),
        pitchLine([
          [24.84, GOAL_LINE_X],
          [24.84, 99.5],
          [43.16, 99.5],
          [43.16, GOAL_LINE_X],
        ]),
        pitchLine([
          [30.34, GOAL_LINE_X],
          [30.34, 107],
          [37.66, 107],
          [37.66, GOAL_LINE_X],
        ]),
        pitchLine(arcPoints(34, 94, 9.15, 217, 323)),
        pitchLine(arcPoints(34, HALF_WAY_X, 9.15, 0, 180)),
        {
          type: "scatter",
          silent: true,
          tooltip: { show: false },
          data: [[34, 94]],
          symbolSize: 4,
          itemStyle: { color: "rgba(226, 247, 241, 0.9)" },
          z: 2,
        },
        {
          type: "scatter",
          data: rows.map((shot) => ({
            value: [shot.y, shot.x, shot.xg ?? 0],
            shot,
            symbolSize: 10 + Math.min(21, Math.sqrt(Math.max(0, shot.xg ?? 0)) * 24),
            itemStyle: {
              color:
                shot.outcome === "Goal"
                  ? "#ffd166"
                  : shot.outcome === "AttemptSaved"
                    ? "#45dcc7"
                    : "rgba(240, 247, 246, 0.88)",
              borderColor: shot.outcome === "Goal" ? "#7a5000" : "#06323a",
              borderWidth: shot.outcome === "Goal" ? 2 : 1.25,
              opacity: 0.94,
              shadowBlur: shot.outcome === "Goal" ? 7 : 0,
              shadowColor: "rgba(255, 209, 102, 0.45)",
            },
          })),
          z: 4,
        },
      ],
    }),
    [matchById, rows, teamId],
  );

  const ariaSummary =
    `${team.name}${mode === "created" ? "创造" : "承受"}射门 ${summary.shots} 次，` +
    `射正 ${onTargetDisplay} 次${onTargetIsOfficial ? "(官方口径)" : ""}，` +
    `进球 ${summary.goals} 个，xG ${summary.xg == null ? "未提供" : summary.xg.toFixed(2)}；` +
    `${coverage?.shot_covered_games ?? 0}/${coverage?.matched_games ?? 0} 场有逐次射门数据` +
    (onTargetIsOfficial && official.covered < official.total
      ? `，其中 ${official.total - official.covered} 场缺官方射正数据未计入`
      : "") +
    "。";

  function changeTeam(nextTeamId: number) {
    setTeamId(nextTeamId);
    setScope(preferredScopeFor(data, nextTeamId));
    setSelectedMatchId(null);
  }

  function changeScope(nextScope: Scope) {
    setScope(nextScope);
    setSelectedMatchId(null);
  }

  function toggleSituation(key: string) {
    setSituations((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  const detailFiltered = situations.length > 0 || bodyPart !== null || half !== "all";
  function resetDetailFilters() {
    setSituations([]);
    setBodyPart(null);
    setHalf("all");
  }

  return (
    <div className={styles.explorer}>
      <div className={styles.reportHeader}>
        <div>
          <p className={styles.eyebrow}>近 {data.window} 场 · 逐次射门研报</p>
          <h3>{team.name}射门落点</h3>
        </div>
        <span className={styles.coverageBadge}>
          {coverage?.shot_covered_games ?? 0}/{coverage?.matched_games ?? 0} 场完整
        </span>
      </div>

      <div
        className={styles.filterPanel}
        aria-label="射门图筛选"
        hidden={isExport}
        style={isExport ? { display: "none" } : undefined}
      >
        <div className={styles.filterRow}>
          <span className={styles.filterLabel}>球队</span>
          <div className={styles.segmented}>
            {data.teams.map((item) => (
              <button
                type="button"
                key={item.team_id}
                className={item.team_id === teamId ? styles.active : undefined}
                aria-pressed={item.team_id === teamId}
                onClick={() => changeTeam(item.team_id)}
              >
                {item.name}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.filterRow}>
          <span className={styles.filterLabel}>视角</span>
          <div className={styles.segmented}>
            <button
              type="button"
              className={mode === "created" ? styles.active : undefined}
              aria-pressed={mode === "created"}
              onClick={() => setMode("created")}
            >
              本队射门
            </button>
            <button
              type="button"
              className={mode === "conceded" ? styles.active : undefined}
              aria-pressed={mode === "conceded"}
              onClick={() => setMode("conceded")}
            >
              对手射门
            </button>
          </div>
          <div className={styles.scopeToggle}>
            <button
              type="button"
              className={scope === "same_league" ? styles.scopeActive : undefined}
              aria-pressed={scope === "same_league"}
              onClick={() => changeScope("same_league")}
            >
              同联赛
            </button>
            <button
              type="button"
              className={scope === "all_covered" ? styles.scopeActive : undefined}
              aria-pressed={scope === "all_covered"}
              onClick={() => changeScope("all_covered")}
            >
              全部赛事
            </button>
          </div>
        </div>
        <div className={styles.filterRow}>
          <span className={styles.filterLabel}>结果</span>
          <div className={styles.chips}>
            {([
              ["all", "全部"],
              // 这个筛选按 Goal+AttemptSaved 选点,AttemptSaved 混了被封堵
              // 的射门——标"射正"会和统计格里的官方口径数字对不上,加括号
              // 说清楚这是逐次射门口径。
              ["on_target", "射正(含被封堵)"],
              ["goal", "进球"],
            ] as Array<[OutcomeFilter, string]>).map(([value, label]) => (
              <button
                type="button"
                key={value}
                className={outcome === value ? styles.chipActive : undefined}
                aria-pressed={outcome === value}
                onClick={() => setOutcome(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className={styles.chips}>
            {([
              ["all", "全场"],
              ["first", "上半场"],
              ["second", "下半场"],
            ] as Array<[HalfFilter, string]>).map(([value, label]) => (
              <button
                type="button"
                key={value}
                className={half === value ? styles.chipActive : undefined}
                aria-pressed={half === value}
                onClick={() => setHalf(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.filterRow}>
          <span className={styles.filterLabel}>射门方式</span>
          <div className={styles.chips}>
            {SITUATION_CHIPS.map((key) => (
              <button
                type="button"
                key={key}
                className={situations.includes(key) ? styles.chipActive : undefined}
                aria-pressed={situations.includes(key)}
                onClick={() => toggleSituation(key)}
              >
                {SHOT_SITUATION_ZH[key] ?? key}
              </button>
            ))}
            {BODY_PARTS.map((key) => (
              <button
                type="button"
                key={key}
                className={bodyPart === key ? styles.chipActive : undefined}
                aria-pressed={bodyPart === key}
                onClick={() => setBodyPart(bodyPart === key ? null : key)}
              >
                {SHOT_TYPE_ZH[key] ?? key}
              </button>
            ))}
            {detailFiltered && (
              <button type="button" className={styles.reset} onClick={resetDetailFilters}>
                重置
              </button>
            )}
          </div>
        </div>
      </div>

      <div
        className={styles.matchRail}
        aria-label="按比赛筛选"
        hidden={isExport}
        style={isExport ? { display: "none" } : undefined}
      >
        <button
          type="button"
          className={selectedMatchId === null ? styles.matchActive : undefined}
          aria-pressed={selectedMatchId === null}
          onClick={() => setSelectedMatchId(null)}
        >
          近 {coverage?.matched_games ?? data.window} 场合计
        </button>
        {coveredMatches.map((match) => (
          <button
            type="button"
            key={match.match_id}
            className={selectedMatchId === match.match_id ? styles.matchActive : undefined}
            aria-pressed={selectedMatchId === match.match_id}
            onClick={() => setSelectedMatchId(match.match_id)}
          >
            <span>{shortDate(match.date_utc)}</span>
            对 {matchOpponent(match, teamId)}
          </button>
        ))}
      </div>

      <div className={styles.statGrid}>
        <div>
          <span>射门</span>
          <strong>{summary.shots}</strong>
        </div>
        <div>
          <span>射正{onTargetIsOfficial ? "*" : ""}</span>
          <strong>{onTargetDisplay}</strong>
        </div>
        <div>
          <span>进球</span>
          <strong>{summary.goals}</strong>
        </div>
        <div>
          <span>xG</span>
          <strong>{summary.xg == null ? "—" : summary.xg.toFixed(2)}</strong>
        </div>
      </div>

      <div className={styles.pitchCard}>
        <div className={styles.pitchTopline}>
          <span>{mode === "created" ? "本队进攻" : "对手进攻"}</span>
          <span>进攻方向 ↑</span>
        </div>
        <EChart
          option={option}
          height={isExport ? height : Math.max(320, Math.min(height, 360))}
          ariaSummary={ariaSummary}
          className={styles.chartInner}
          mode={renderMode}
          showSummary={!isExport}
        />
      </div>

      <div className={styles.legend} aria-label="射门图例">
        <span><i className={styles.goalDot} />进球</span>
        <span><i className={styles.savedDot} />被扑/被挡</span>
        <span><i className={styles.otherDot} />其他射门</span>
        <span className={styles.sizeLegend}><i />点越大，xG 越高</span>
      </div>
      <p className={styles.note}>
        球场统一朝上进攻；数据来自本站已采集比赛。单场切换只改变当前球场与数字，
        不会补造缺失射门。
        {onTargetIsOfficial &&
          "　*射正为球队官方统计口径，与场上被扑/被挡圆点(无法区分门将扑救与后卫封堵)分别计算，二者不必相等。"}
      </p>
    </div>
  );
}
