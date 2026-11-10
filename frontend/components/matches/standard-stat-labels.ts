/** Standard report statistics; prematch aggregates retain their separate contract. */
export type TeamStatLabel = {
  key: string;
  label: string;

  format: "pct" | "num" | "num1" | "unverified";

  unit?: string;
};

export function formatTeamStat(v: number | null | undefined, meta: TeamStatLabel): string {
  if (v == null) return "—";
  if (meta.format === "pct") return `${Math.round(v)}%`;
  if (meta.format === "num1") return v.toFixed(2);
  if (meta.format === "unverified") return `${v}（单位待核实）`;
  return `${Math.round(v)}${meta.unit ?? ""}`;
}

export const TEAM_STAT_LABELS: TeamStatLabel[] = [
  { key: "possession_percent", label: "控球率", format: "pct" },

  { key: "expected_goals", label: "官方统计 xG", format: "num1" },

  { key: "expected_goals_on_target", label: "官方统计 xGOT", format: "num1" },

  { key: "expected_goals_open_play", label: "运动战 xG", format: "num1" },
  { key: "expected_goals_set_play", label: "定位球 xG", format: "num1" },
  { key: "expected_goals_non_penalty", label: "非点球 xG", format: "num1" },

  { key: "shots_total", label: "射门次数", format: "num" },
  { key: "shots_on_target", label: "射正", format: "num" },

  { key: "shots_off_target", label: "射偏", format: "num" },
  { key: "big_chances", label: "绝佳机会", format: "num" },
  { key: "big_chances_missed", label: "错失绝佳机会", format: "num" },
  { key: "shots_inside_penalty_area", label: "禁区内射门", format: "num" },
  { key: "shots_outside_penalty_area", label: "禁区外射门", format: "num" },
  { key: "shots_hit_woodwork", label: "击中门框", format: "num" },

  { key: "shots_blocked", label: "射门被封堵", format: "num" },

  { key: "touches_in_opposition_box", label: "对方禁区内触球", format: "num" },
  { key: "passes_attempted", label: "传球总数", format: "num" },
  { key: "passes_completed", label: "成功传球", format: "num" },
  { key: "passes_completed_own_half", label: "己方半场传球", format: "num" },
  { key: "passes_completed_opposition_half", label: "对方半场传球", format: "num" },
  { key: "long_passes_completed", label: "成功长传", format: "num" },
  { key: "crosses_completed", label: "成功传中", format: "num" },
  { key: "throw_ins", label: "掷界外球", format: "num" },
  { key: "corners_taken", label: "角球", format: "num" },
  { key: "tackles", label: "抢断", format: "num" },
  { key: "interceptions", label: "拦截", format: "num" },

  { key: "shots_blocked_defensively", label: "封堵对方射门", format: "num" },
  { key: "clearances", label: "解围", format: "num" },
  { key: "saves", label: "门将扑救", format: "num" },
  { key: "duels_won", label: "对抗成功", format: "num" },
  { key: "ground_duels_won", label: "地面对抗成功", format: "num" },
  { key: "aerial_duels_won", label: "争顶成功", format: "num" },
  { key: "dribbles_completed", label: "成功过人", format: "num" },
  { key: "fouls_committed", label: "犯规", format: "num" },
  { key: "offsides", label: "越位", format: "num" },
  { key: "yellow_cards", label: "黄牌", format: "num" },
  { key: "red_cards", label: "红牌", format: "num" },

  { key: "distance_covered", label: "跑动距离", format: "unverified" },
  { key: "running_distance", label: "中高速跑动", format: "unverified" },
  { key: "sprinting_distance", label: "冲刺跑动", format: "unverified" },
  { key: "walking_distance", label: "步行", format: "unverified" },
  { key: "sprints", label: "冲刺次数", format: "num", unit: "次" },
];

export const TEAM_STAT_GROUPS: { key: string; label: string; statKeys: string[] }[] = [
  {
    key: "top_stats", label: "重点数据",
    statKeys: [
      "possession_percent", "expected_goals", "shots_total", "shots_on_target",
      "touches_in_opposition_box", "big_chances", "big_chances_missed", "passes_completed",
      "yellow_cards", "corners_taken",
    ],
  },
  {
    key: "shots", label: "射门",
    statKeys: [
      "shots_total", "shots_off_target", "shots_on_target", "shots_blocked",
      "shots_hit_woodwork", "shots_inside_penalty_area", "shots_outside_penalty_area",
    ],
  },
  {
    key: "expected_goals", label: "预期进球",
    statKeys: [
      "expected_goals", "expected_goals_open_play", "expected_goals_set_play",
      "expected_goals_non_penalty", "expected_goals_on_target",
    ],
  },
  {
    key: "passes_attempted", label: "传球",
    statKeys: [
      "passes_attempted", "passes_completed", "passes_completed_own_half", "passes_completed_opposition_half",
      "long_passes_completed", "crosses_completed", "throw_ins",
      "touches_in_opposition_box", "offsides",
    ],
  },
  {
    key: "defence", label: "防守",
    statKeys: ["tackles", "interceptions", "shots_blocked_defensively", "clearances", "saves"],
  },
  {
    key: "duels", label: "对抗",
    statKeys: ["duels_won", "ground_duels_won", "aerial_duels_won", "dribbles_completed"],
  },
  {
    key: "discipline", label: "纪律",
    statKeys: ["yellow_cards", "red_cards", "fouls_committed"],
  },

  {
    key: "physical", label: "跑动",
    statKeys: [
      "distance_covered", "running_distance",
      "sprinting_distance", "walking_distance",
      "sprints",
    ],
  },
];
