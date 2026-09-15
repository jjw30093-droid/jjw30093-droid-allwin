/**
 * 象限图详情面板的取数纯逻辑(2026-09-09):指标定义、联赛均值、联赛内排名。
 *
 * 诚实纪律:
 * - 缺值一律返回 null,不补 0(0 在 xG/零封语境里是有意义的真实值);
 * - 派生指标(点球 xG = 总 xG − 非点球 xG)任一操作数缺失即整体缺失;
 * - 均值与排名的分母只数真正有值的球队,不是 rows.length;
 * - 目标球队自身缺值时排名为 null(不是"排最后")。
 */

import type { TeamSeasonStatRow } from "@/lib/api-v1";

/** 小样本门槛。挂在**指标**上而不是视角上:噪声下限是指标本身的属性——
 *  "定位球 xG 占比"在赛季累计定位球 xG 只有 0.4 的队身上是噪声,它出现在
 *  哪个视角里都一样。视角想更严格,就声明一个新指标,不是给视角加参数。 */
export type SampleRule = {
  /** 最少已赛场次。缺 matches_played 视为不达标——门槛的意义就是宁可不画,
   *  "场次未知"不能假设它够。 */
  minMatches?: number;
  /** 分母累计量下限。比值本身看不出分母有多大:30% 这个数字,分母是 12.0
   *  还是 0.4,是两件完全不同的事。of() 取**赛季累计量**(不是场均);
   *  label/unit/digits 会逐字出现在"隐藏了 N 支"的说明里,不许写内部字段名。 */
  minVolume?: {
    of: (r: TeamSeasonStatRow) => number | null;
    atLeast: number;
    label: string;
    unit: string;
    digits: number;
  };
};

export type MetricDef = {
  id: string;
  label: string;
  unit: string;
  digits: number;
  /** true = 数值越小越好(预期失球 / 犯规 / 牌),排名升序 */
  lowerIsBetter?: boolean;
  /** 赛季累计而非场均(零封场次 / BTTS 场数),面板上要标注口径 */
  perSeason?: boolean;
  /** 口径一句话(比值类必填),详情面板与视角说明逐字复用,
   *  例如"赛季累计定位球 xG ÷ 赛季累计非点球 xG,逐场配对后求和,
   *  不是两个场均值相除"。 */
  caliber?: string;
  /** performance = 可比优劣;style = 打法特征,不代表强弱,页面只能写
   *  "偏向/较多",不能写"更强";outcome_variance = 短期结果记录,不是
   *  可持续能力(CLAUDE.md 之外、backend/metrics/registry.py 的同一套分类)。 */
  semantic?: "performance" | "style" | "outcome_variance";
  /** 只在这个指标**被当作坐标轴**时生效。详情面板里的补充指标照常显示
   *  真实值——样本小是"不画点"的理由,不是"把已知事实藏起来"的理由。 */
  sample?: SampleRule;
  /** 分子分母都是小整数的比值指标(如角球成射率):详情面板按原始计数
   *  展示"11/48",不是只给百分比——分母小的比率,单独一个百分比数字
   *  容易被读成比实际更精确的结论(方案护栏要求)。 */
  fraction?: (r: TeamSeasonStatRow) => { num: number; den: number } | null;
  /** 覆盖率类免责披露用的额外样本量(如"场均门将扑救超额"排除 xGOT
   *  缺失后,纳入求和的有效射正次数)——只有极少数指标需要,读
   *  TeamRatioValue.sample_count,不是 value() 本身的一部分。
   *  sampleCountLabel 是这个数字的人话说明,例如"有 xGOT 的射正"。 */
  sampleCountOf?: (r: TeamSeasonStatRow) => number | null;
  sampleCountLabel?: string;
  value: (r: TeamSeasonStatRow) => number | null;
};

/** 参照整季场次数(本站收录联赛里最常见的顶级联赛赛制)——一个直接了当、
 *  有据可查的默认值,不是精确公式,可以后续按真实使用反馈调整。只用来把
 *  "当前筛选窗口大致是整季的几分之几"换算成一个缩放系数,不代表任何一个
 *  具体联赛真实赛季总轮次。 */
const REFERENCE_SEASON_MATCHES = 38;
/** 主/客场筛选(不带 recency)没有一个像 recency 那样直接可查的"窗口场次数"
 *  ——主客场大致各占整季一半,用 0.5 近似。同样是可调整的启发式。 */
const VENUE_ONLY_SCALE = 0.5;

/** 2026-09-14"最近 N 场/主客场"筛选新增:样本门槛按当前筛选窗口大小成比例
 *  缩小,不是原样保留整赛季门槛——否则"最近 3 场"下几乎所有比率型指标都会
 *  判定"样本不足"而不画,筛选功能形同虚设。`recency` 优先(它是用户显式限定
 *  的更小窗口);只筛主客场、不筛 recency 时用 VENUE_ONLY_SCALE 近似;
 *  都没筛(默认态)返回 1,不缩放,与筛选功能上线前逐字节相同。 */
export function windowScaleFor(recency: number | null | undefined, venue: string | undefined): number {
  if (recency != null) return recency / REFERENCE_SEASON_MATCHES;
  if (venue === "home" || venue === "away") return VENUE_ONLY_SCALE;
  return 1;
}

/** 样本是否达标?两条门槛都要过,缺任一所需字段一律判不达标(不能假设够)。
 *  `windowScale`(2026-09-14 新增,默认 1 = 不缩放)按当前筛选窗口把门槛
 *  等比缩小——筛"最近 5 场"时,原本"≥6 场"的门槛不该继续原样卡住几乎
 *  所有球队。 */
export function meetsSample(
  r: TeamSeasonStatRow,
  m: { sample?: SampleRule },
  windowScale = 1,
): boolean {
  const s = m.sample;
  if (!s) return true;
  if (s.minMatches != null) {
    const threshold = Math.max(1, Math.round(s.minMatches * windowScale));
    const mp = num(r.matches_played);
    if (mp == null || mp < threshold) return false;
  }
  if (s.minVolume) {
    const threshold = s.minVolume.atLeast * windowScale;
    const v = s.minVolume.of(r);
    if (v == null || v < threshold) return false;
  }
  return true;
}

/** 门槛的人话版本,进"隐藏了 N 支"的说明。没有门槛返回空串。
 *  `windowScale` 同 meetsSample,缺省 1 时文案与筛选功能上线前逐字节相同。 */
export function sampleText(m: { sample?: SampleRule }, windowScale = 1): string {
  const s = m.sample;
  if (!s) return "";
  const parts: string[] = [];
  if (s.minMatches != null) {
    const threshold = Math.max(1, Math.round(s.minMatches * windowScale));
    parts.push(`不少于 ${threshold} 场`);
  }
  if (s.minVolume) {
    const threshold = s.minVolume.atLeast * windowScale;
    parts.push(`${s.minVolume.label}不少于 ${threshold.toFixed(s.minVolume.digits)}${s.minVolume.unit}`);
  }
  return parts.join("、");
}

/** 选中态与排名的身份键。不能用对象引用:ECharts 在 setOption 里会克隆
 *  data[i] 的嵌套对象,params.data.pt 与原始对象引用不相等。 */
export function teamKey(t: { team_id?: number | null; name: string }): string {
  return t.team_id != null ? `id:${t.team_id}` : `name:${t.name}`;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function columnMetric(
  column: keyof TeamSeasonStatRow,
  def: Omit<MetricDef, "id" | "value">,
): MetricDef {
  return { id: column, ...def, value: (r) => num(r[column]) };
}

export const METRICS = {
  shotsOnTarget: columnMetric("avg_shots_on_target", { label: "场均射正", unit: "脚", digits: 1 }),
  cleanSheets: columnMetric("clean_sheets", { label: "零封场次", unit: "场", digits: 0, perSeason: true }),
  bttsPct: columnMetric("btts_pct", { label: "双方进球率", unit: "%", digits: 0 }),
  nonPenXg: columnMetric("avg_expected_goals_non_penalty", { label: "场均非点球 xG", unit: "", digits: 2 }),
  corners: columnMetric("avg_corners", { label: "场均角球", unit: "个", digits: 1 }),
  xgot: columnMetric("avg_expected_goals_on_target", { label: "场均射正 xG(xGOT)", unit: "", digits: 2 }),
  possession: columnMetric("avg_possession", { label: "控球率", unit: "%", digits: 1, semantic: "style" }),
  fouls: columnMetric("avg_fouls", { label: "场均犯规", unit: "次", digits: 1, semantic: "style" }),
  penXg: {
    id: "penalty_xg",
    label: "场均点球 xG",
    unit: "",
    digits: 2,
    value: (r) => {
      const total = num(r.avg_expected_goals);
      const nonPen = num(r.avg_expected_goals_non_penalty);
      if (total == null || nonPen == null) return null;
      return Math.max(0, total - nonPen);
    },
  } satisfies MetricDef,
  xgPerShot: {
    id: "xg_per_shot",
    label: "每脚射门 xG",
    unit: "",
    digits: 3,
    value: (r) => {
      const xg = num(r.avg_expected_goals);
      const shots = num(r.avg_total_shots);
      if (xg == null || shots == null || shots <= 0) return null;
      return xg / shots;
    },
  } satisfies MetricDef,

  // ── 象限图三个既有视角的轴(2026-09-14 从 quadrantViews.ts 的 Axis 搬进来,
  //    label/unit/digits 逐字节不变,不改变任何已发布文案)──────────────
  xg: columnMetric("avg_expected_goals", {
    label: "场均预期进球 xG", unit: "", digits: 2, semantic: "performance",
  }),
  xga: columnMetric("avg_expected_goals_conceded", {
    label: "场均预期失球 xGA", unit: "", digits: 2, lowerIsBetter: true, semantic: "performance",
  }),
  openPlayXg: columnMetric("avg_expected_goals_open_play", {
    label: "场均运动战 xG", unit: "", digits: 2, semantic: "performance",
  }),
  setPlayXg: columnMetric("avg_expected_goals_set_play", {
    label: "场均定位球 xG", unit: "", digits: 2, semantic: "performance",
  }),
  totalShots: columnMetric("avg_total_shots", {
    label: "场均射门数", unit: "脚", digits: 1, semantic: "performance",
  }),

  // ── 比率型指标:一律读后端预先配对求和的 ratios 字段,前端不做除法
  //    (也就不存在"两个场均值相除"的配对误差)──────────────────────────
  oppHalfPassShare: {
    id: "opp_half_pass_share",
    label: "前场成功传球占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "成功传球里有多大比例发生在对方半场(分母是成功传球,不是传球尝试总数)——本站用现有数据算的代理指标,不是 Opta/StatsBomb 的官方 Field Tilt。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.opp_half_pass_share?.denominator),
        atLeast: 2000,
        label: "赛季累计成功传球",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.opp_half_pass_share?.value),
  } satisfies MetricDef,

  // 包二:站长点名的方向。分母是运动战+定位球 xG 之和(不含点球),不是
  // expected_goals_non_penalty 字段本身——两者 99.7% 场次一致但口径不同,
  // 详见 backend/silver/ratio_metrics.py 同一天的注释。绝不写"占总 xG"。
  setPieceXgShare: {
    id: "set_piece_xg_share",
    label: "定位球 xG 占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计定位球 xG ÷ 赛季累计(运动战 xG + 定位球 xG),逐场配对后求和,不是两个场均值相除;分母不含点球,也不是「占总 xG」。",
    sample: {
      minMatches: 8,
      minVolume: {
        of: (r) => num(r.ratios?.set_piece_xg_share?.denominator),
        atLeast: 8,
        label: "赛季累计运动战+定位球 xG",
        unit: "",
        digits: 1,
      },
    },
    value: (r) => num(r.ratios?.set_piece_xg_share?.value),
  } satisfies MetricDef,
  setPieceXgaShare: {
    id: "set_piece_xga_share",
    label: "失球端定位球占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "对手赛季累计定位球 xG ÷ 对手赛季累计(运动战 xG + 定位球 xG),口径与「定位球 xG 占比」相同,取自同场对手。",
    sample: {
      minMatches: 8,
      minVolume: {
        of: (r) => num(r.ratios?.set_piece_xga_share?.denominator),
        atLeast: 8,
        label: "对手赛季累计运动战+定位球 xG",
        unit: "",
        digits: 1,
      },
    },
    value: (r) => num(r.ratios?.set_piece_xga_share?.value),
  } satisfies MetricDef,

  // 包三。对手每脚射门 xG——"对手每脚射门 xG"与既有 xgPerShot(我方口径)
  // 同构,只是取自对手行,用于「攻防质量」视角的 y 轴(越低越好)。
  oppXgPerShot: {
    id: "opp_xg_per_shot",
    label: "对手每脚射门 xG",
    unit: "",
    digits: 3,
    lowerIsBetter: true,
    semantic: "performance",
    caliber: "对手赛季累计 xG ÷ 对手赛季累计射门数,逐场配对后求和,取自同场对手。数字越低说明防线把对手逼到了更差的射门位置。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.opp_xg_per_shot?.denominator),
        atLeast: 60,
        label: "对手赛季累计射门数",
        unit: "脚",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.opp_xg_per_shot?.value),
  } satisfies MetricDef,

  shotAccuracy: {
    id: "shot_accuracy",
    label: "射正率",
    unit: "%",
    digits: 1,
    semantic: "performance",
    caliber: "赛季累计射正 ÷ 赛季累计(射正+射偏)——分母已排除被封堵射门,不是除以总射门数。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.shot_accuracy?.denominator),
        atLeast: 60,
        label: "赛季累计非封堵射门",
        unit: "脚",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.shot_accuracy?.value),
  } satisfies MetricDef,

  boxShotShare: {
    id: "box_shot_share",
    label: "禁区内射门占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计禁区内射门 ÷ 赛季累计(禁区内+禁区外射门),逐场配对后求和。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.box_shot_share?.denominator),
        atLeast: 60,
        label: "赛季累计射门数",
        unit: "脚",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.box_shot_share?.value),
  } satisfies MetricDef,

  // 绝佳机会把握率:样本小(一季没几个大机会)时噪音很大,门槛按"累计大机会数"设。
  bigChanceConversion: {
    id: "big_chance_conversion",
    label: "绝佳机会把握率",
    unit: "%",
    digits: 1,
    semantic: "performance",
    caliber: "赛季累计(大机会−错失大机会)÷ 赛季累计大机会,逐场配对后求和。样本越小越容易被一两次运气波动带偏。",
    sample: {
      minMatches: 10,
      minVolume: {
        of: (r) => num(r.ratios?.big_chance_conversion?.denominator),
        atLeast: 25,
        label: "赛季累计大机会",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.big_chance_conversion?.value),
  } satisfies MetricDef,

  // 争顶优势占比:两队没有单独的"争顶总数"字段,分母是双方 aerials_won 之和
  // 的近似——caliber 里如实说明这个局限,不假装是精确的"争顶成功率"。
  aerialWinShare: {
    id: "aerial_win_share",
    label: "争顶优势占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计本队争顶成功 ÷ 赛季累计(本队+对手争顶成功之和)——没有单独的“争顶总数”字段,用双方赢下的次数之和近似全场争顶总量。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.aerial_win_share?.denominator),
        atLeast: 300,
        label: "赛季累计双方争顶成功之和",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.aerial_win_share?.value),
  } satisfies MetricDef,

  // 快速反击 xG 占比:取自射门级数据(fact_shotmap.Situation),分母是全部
  // 非点球 xG,与「定位球 xG 占比」同一惯例(不含点球)。
  fastBreakXgShare: {
    id: "fast_break_xg_share",
    label: "快速反击 xG 占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计快速反击 xG ÷ 赛季累计非点球 xG(射门级数据,按 Situation 分类,逐场配对后求和)。",
    sample: {
      minMatches: 12,
      minVolume: {
        of: (r) => num(r.ratios?.fast_break_xg_share?.denominator),
        atLeast: 15,
        label: "赛季累计非点球 xG",
        unit: "",
        digits: 1,
      },
    },
    value: (r) => num(r.ratios?.fast_break_xg_share?.value),
  } satisfies MetricDef,

  // 包四(第一批):禁区压制。touches_opp_box 只有这两个赛季覆盖率 100%,
  // 后端 RatioSpec 用 eligible_seasons 在 silver 层直接不产出其它赛季的行,
  // 前端这里不需要额外判断赛季——没有数据时 value 自然是 null,
  // 走既有的"missing"隐藏路径,行为和其它缺数据场景一致。
  boxTouchShare: {
    id: "box_touch_share",
    label: "禁区触球占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计本队对方禁区触球 ÷ 赛季累计(本队+对手对方禁区触球之和)。仅 2024/2025、2025/2026 两个赛季有数据(其余赛季 touches_opp_box 随机缺失,球队间不可比)。这是本站目前最接近 Field Tilt 的字段,但不是 Opta/StatsBomb 的官方 Field Tilt。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.box_touch_share?.denominator),
        atLeast: 40,
        label: "赛季累计双方禁区触球之和",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.box_touch_share?.value),
  } satisfies MetricDef,

  npxgPerBoxTouch: {
    id: "npxg_per_box_touch",
    label: "每次禁区触球 npxG",
    unit: "",
    digits: 3,
    semantic: "performance",
    caliber: "赛季累计非点球 xG ÷ 赛季累计对方禁区触球。仅 2024/2025、2025/2026 两个赛季有数据。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.npxg_per_box_touch?.denominator),
        atLeast: 20,
        label: "赛季累计对方禁区触球",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.npxg_per_box_touch?.value),
  } satisfies MetricDef,

  // 防守动作密度——不是 PPDA(没有动作坐标,无法限定逼抢区域)。
  defActionDensity: {
    id: "def_action_density",
    label: "防守动作密度(每百次对手传球)",
    unit: "次",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计本队(抢断+拦截+犯规)÷ 赛季累计对手传球数 × 100。不是 PPDA——没有动作坐标,无法限定逼抢发生在哪个区域,铁桶阵与高位压迫可能拿到相近的数字。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.def_action_density?.denominator),
        atLeast: 2000,
        label: "赛季累计对手传球数",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.def_action_density?.value),
  } satisfies MetricDef,

  oppTerritoryShare: {
    id: "opp_territory_share",
    label: "对手前场成功传球占比",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "对手赛季累计前场成功传球 ÷ 对手赛季累计成功传球,与「前场成功传球占比」同一公式,取自同场对手。",
    sample: {
      minMatches: 6,
      minVolume: {
        of: (r) => num(r.ratios?.opp_territory_share?.denominator),
        atLeast: 2000,
        label: "对手赛季累计成功传球",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.opp_territory_share?.value),
  } satisfies MetricDef,

  cornerShotRate: {
    id: "corner_shot_rate",
    label: "角球成射率",
    unit: "%",
    digits: 1,
    semantic: "style",
    caliber: "赛季累计(由角球产生的射门次数)÷ 赛季累计角球数。分母通常较小,详情面板按原始计数展示(如 11/48),不只给百分比。",
    sample: {
      minMatches: 10,
      minVolume: {
        of: (r) => num(r.ratios?.corner_shot_rate?.denominator),
        atLeast: 50,
        label: "赛季累计角球数",
        unit: "个",
        digits: 0,
      },
    },
    fraction: (r) => {
      const num_ = num(r.ratios?.corner_shot_rate?.numerator);
      const den = num(r.ratios?.corner_shot_rate?.denominator);
      return num_ != null && den != null ? { num: num_, den } : null;
    },
    value: (r) => num(r.ratios?.corner_shot_rate?.value),
  } satisfies MetricDef,

  finishingDelta: {
    id: "finishing_delta",
    label: "场均终结超额",
    unit: "球",
    digits: 2,
    semantic: "outcome_variance",
    caliber: "场均(非点球进球 − 非点球预期进球),进球按受益方计乌龙球。这不是稳定的终结能力,只是短期窗口的结果记录——调研认为这类差值跨赛季的相关性接近零。",
    sample: {
      minMatches: 15,
      minVolume: {
        of: (r) => num(r.ratios?.finishing_delta?.sample_count),
        atLeast: 150,
        label: "赛季累计非点球射门数",
        unit: "次",
        digits: 0,
      },
    },
    value: (r) => num(r.ratios?.finishing_delta?.value),
  } satisfies MetricDef,

  gkSavesAboveExpected: {
    id: "gk_saves_above_expected",
    label: "场均门将扑救超额",
    unit: "球",
    digits: 2,
    semantic: "outcome_variance",
    caliber: "场均(对手射正的预期进球质量 − 实际非点球失球),只统计非点球、非乌龙、且有真实 xGOT 数据的射正。这不是稳定的门将能力评价,只是短期窗口的近期记录,不代表未来表现。",
    sample: {
      minMatches: 10,
      minVolume: {
        of: (r) => num(r.ratios?.gk_saves_above_expected?.sample_count),
        atLeast: 60,
        label: "赛季累计有效xGOT射正次数",
        unit: "次",
        digits: 0,
      },
    },
    sampleCountOf: (r) => num(r.ratios?.gk_saves_above_expected?.sample_count),
    sampleCountLabel: "有 xGOT 数据的对手射正",
    value: (r) => num(r.ratios?.gk_saves_above_expected?.value),
  } satisfies MetricDef,

  // 「终结记录」视角的 x 轴。直接复用 finishing_delta 已经在算的"赛季累计
  // 非点球射门数"(TeamRatioValue.sample_count)除以已赛场次,不新开一条
  // 后端聚合——两个视角共用同一份底层射门量,不是巧合,是同一件事的两种
  // 展示方式(总量场均化 vs 结果超额)。
  nonPenaltyShotsPerMatch: {
    id: "non_penalty_shots_per_match",
    label: "场均非点球射门",
    unit: "脚",
    digits: 1,
    semantic: "performance",
    caliber: "赛季累计非点球射门数 ÷ 已赛场次(与「场均终结超额」共用同一份射门量)。",
    value: (r) => {
      const shots = num(r.ratios?.finishing_delta?.sample_count);
      const mp = num(r.matches_played);
      return shots != null && mp != null && mp > 0 ? shots / mp : null;
    },
  } satisfies MetricDef,
} as const;

export function formatMetric(v: number | null, m: MetricDef): string {
  if (v == null) return "—";
  return `${v.toFixed(m.digits)}${m.unit}`;
}

/** 分子分母都是原始计数的字符串(如"11/48"),给 m.fraction 用的指标专用;
 *  没有该字段、或该队这两个数缺任一都返回 null(不臆造一个假分数)。 */
export function formatFraction(r: TeamSeasonStatRow, m: MetricDef): string | null {
  const f = m.fraction?.(r);
  return f ? `${f.num}/${f.den}` : null;
}

/** 分母只数真正有值的球队;全联赛都缺 → null(与"该队缺"区分开,面板文案不同)。 */
export function leagueMean(
  rows: TeamSeasonStatRow[],
  m: MetricDef,
): { mean: number; n: number } | null {
  const vals = rows.map((r) => m.value(r)).filter((v): v is number => v != null);
  if (!vals.length) return null;
  return { mean: vals.reduce((a, b) => a + b, 0) / vals.length, n: vals.length };
}

/**
 * 并列同名次(1224 制):两队并列第 2 时下一队是第 4。
 * lowerIsBetter 时升序排(第 1 = 最小)。values 只应包含真正有值的球队。
 */
export function competitionRank(
  values: number[],
  mine: number,
  lowerIsBetter = false,
): { rank: number; total: number } {
  const better = values.filter((v) => (lowerIsBetter ? v < mine : v > mine)).length;
  return { rank: better + 1, total: values.length };
}

/** 联赛内排名;目标球队自身缺值 → null(不是"排最后"),分母只数有值的队。 */
export function rankOf(
  rows: TeamSeasonStatRow[],
  m: MetricDef,
  targetKey: string,
): { rank: number; total: number } | null {
  const scored = rows
    .map((r) => ({ key: teamKey(r.team), v: m.value(r) }))
    .filter((e): e is { key: string; v: number } => e.v != null);
  const me = scored.find((e) => e.key === targetKey);
  if (!me) return null;
  return competitionRank(
    scored.map((e) => e.v),
    me.v,
    m.lowerIsBetter,
  );
}
