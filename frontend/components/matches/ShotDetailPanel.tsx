"use client";

/**
 * 射门详情翻页面板(2026-08-24,复刻 FotMob 点击射门弹出详情、上一条/下一条
 * 翻页的效果)。固定位置卡片,挂在射门落点图下方——不做跟随选中点定位的
 * 悬浮弹层(全站没有可复用的悬浮弹层组件,FotMob 自己这个面板叫
 * FullscreenShotInformationContainer,截图显示占相当篇幅,不是跟随光标的
 * 小气泡)。
 *
 * 2026-08-25:未选中时 **return null**(此前"始终挂载 + 占位文案"空占
 * ~93px)——布局不跳动由调用方 ShotMapChart 的 .panelSlot grid 行高过渡
 * 负责,空态说明由球场下方常驻提示行承担。同日新增:关闭按钮(Esc 关闭在
 * ShotMapChart 里)、球门框示意图(GoalMouthDiagram,坐标语义已验证)。
 *
 * 2026-08-26 布局重做(对齐 FotMob shotmap_shot_item 的卡片结构,站长反馈
 * 旧版"一行流水账"太丑):导航行(保留)→ 球员身份行(头像+姓名+球队+
 * 分钟)→ **标签-数值行**(结果/情境/部位/xG/xGOT,数值右对齐、tabular
 * 数字)→ 球门框可视化独占一块居中区域;窄屏纵向堆叠、宽屏左右两栏。
 * 球门框走 onGoalShot 主路径(FotMob 反编译公式,含 zoomRatio 缩放),
 * 被封堵隐藏标记,历史场次退回 goal_crossed 旧路径——见 GoalMouthDiagram
 * 头注释。
 *
 * 翻页在数组位置上做(onPrev/onNext 由调用方 ShotMapChart.tsx 实现),不
 * 依赖 shot_id——历史比赛(未重新抓取)shot_id 可能整场为空。
 */

import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import type { MatchReportResponse } from "@/lib/api-v1";
import { SHOT_SITUATION_ZH, SHOT_TYPE_ZH } from "@/components/matches/zh";
import { outcomeLabelFor } from "./ShotMapChart";
import {
  GoalMouthDiagram,
  buildGoalMouthSummary,
  buildOnGoalShotSummary,
  normalizeGoalMouthPoint,
  normalizeOnGoalShot,
} from "./GoalMouthDiagram";
import styles from "./ShotDetailPanel.module.css";

type MatchReport = Extract<MatchReportResponse, { available: true }>;
type Shot = MatchReport["shots"][number];

export function ShotDetailPanel({
  shot,
  homeName,
  awayName,
  homeCrestUrl,
  awayCrestUrl,
  homeColor,
  awayColor,
  shirtNumberByPlayerId,
  onPrev,
  onNext,
  onClose,
  hasPrev,
  hasNext,
  position,
  total,
}: {
  shot: Shot | null;
  homeName: string;
  awayName: string;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  /** 已经过 resolveMatchColors 的球队色(球门框示意图的标记色)。 */
  homeColor?: string;
  awayColor?: string;
  shirtNumberByPlayerId?: Record<string, string>;
  onPrev: () => void;
  onNext: () => void;
  /** 关闭面板(清除选中)。2026-08-25 前没有任何清除入口。 */
  onClose?: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  /** 当前选中射门在 plotted 里的 1-based 位置;没有选中时为 null。 */
  position: number | null;
  total: number;
}) {
  if (!shot) return null;

  const teamName = shot.is_home ? homeName : awayName;
  const crestUrl = shot.is_home ? homeCrestUrl : awayCrestUrl;
  const markerColor = (shot.is_home ? homeColor : awayColor) ?? "var(--brand-teal)";
  const shirtNumber = shirtNumberByPlayerId?.[shot.player_id];
  const situationLabel = shot.situation
    ? (SHOT_SITUATION_ZH[shot.situation] ?? shot.situation)
    : null;
  const shotTypeLabel = shot.shot_type ? (SHOT_TYPE_ZH[shot.shot_type] ?? shot.shot_type) : null;
  // 球门框三条路径的输入(优先级与 GoalMouthDiagram 一致):被封堵隐藏
  // 标记(FotMob 行为)> onGoalShot 主路径 > goal_crossed 旧路径。
  const blocked = shot.is_blocked === true;
  const onGoal = normalizeOnGoalShot(
    shot.on_goal_shot_x,
    shot.on_goal_shot_y,
    shot.on_goal_shot_zoom_ratio,
  );
  const goalMouthPoint = normalizeGoalMouthPoint(shot.goal_crossed_y, shot.goal_crossed_z);
  const goalMouthSummary = blocked
    ? "" // 被封堵分支的文案由 GoalMouthDiagram 自己给(BLOCKED_GOAL_MOUTH_TEXT)
    : onGoal
      ? buildOnGoalShotSummary(onGoal, goalMouthPoint)
      : goalMouthPoint
        ? buildGoalMouthSummary(goalMouthPoint)
        : "";

  const statRows: { label: string; value: string; numeric: boolean }[] = [
    { label: "结果", value: outcomeLabelFor(shot), numeric: false },
    { label: "情境", value: situationLabel ?? "—", numeric: false },
    { label: "部位", value: shotTypeLabel ?? "—", numeric: false },
    { label: "预期进球 xG", value: shot.xg != null ? shot.xg.toFixed(2) : "—", numeric: true },
    {
      label: "射正预期进球 xGOT",
      value: shot.xgot != null ? shot.xgot.toFixed(2) : "—",
      numeric: true,
    },
  ];

  return (
    <div className={styles.panel}>
      <div className={styles.nav}>
        <button
          type="button"
          className={styles.navButton}
          onClick={onPrev}
          disabled={!hasPrev}
          aria-label="上一条射门"
        >
          ‹
        </button>
        {position != null && (
          <span className={styles.navPosition}>
            {position} / {total}
          </span>
        )}
        <span className={styles.navRight}>
          <button
            type="button"
            className={styles.navButton}
            onClick={onNext}
            disabled={!hasNext}
            aria-label="下一条射门"
          >
            ›
          </button>
          {onClose && (
            <button
              type="button"
              className={styles.navButton}
              onClick={onClose}
              aria-label="关闭射门详情"
            >
              ×
            </button>
          )}
        </span>
      </div>

      <div className={styles.body}>
        <div className={styles.infoCol}>
          <div className={styles.playerRow}>
            <PlayerAvatar
              playerId={shot.player_id}
              playerName={shot.player_name ?? shot.player_id}
              shirtNumber={shirtNumber}
              size={40}
            />
            <div className={styles.playerMeta}>
              <span className={styles.playerName}>{shot.player_name ?? shot.player_id}</span>
              <span className={styles.teamRow}>
                <TeamBadge teamName={teamName} crestUrl={crestUrl} size={24} />
                <span className={styles.teamName}>{teamName}</span>
                {shot.minute != null && (
                  <span className={`${styles.minute} num`}>{shot.minute}&#39;</span>
                )}
              </span>
            </div>
          </div>
          <dl className={styles.statList}>
            {statRows.map((row) => (
              <div key={row.label} className={styles.statRow}>
                <dt className={styles.statLabel}>{row.label}</dt>
                <dd className={row.numeric ? `${styles.statValue} num` : styles.statValue}>
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
        <div className={styles.goalMouth}>
          <GoalMouthDiagram
            point={goalMouthPoint}
            onGoal={onGoal}
            blocked={blocked}
            color={markerColor}
            summary={goalMouthSummary}
            outsideFrame={shot.outcome === "Miss" || shot.outcome === "Post"}
          />
        </div>
      </div>
    </div>
  );
}
