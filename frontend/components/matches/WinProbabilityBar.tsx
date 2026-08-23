"use client";

/**
 * 胜平负概率条(DESIGN.md §4「胜平负概率条」组件规格)——单条横向三段堆叠,
 * 宽度=真实百分比,三段用语义色(--win 主胜 / --draw 平 / --loss 客胜),
 * 条下标百分比数字。数据来自 Bet365 1x2 赔率去水
 * (backend/queries/odds.py::latest_1x2_by_match),不是本站模型输出。
 *
 * 无数据(match.win_probability 为 null)→ 整条不占位,不画 33/33/34 这种
 * 假等分,不补 0(站长决策:没有才没有)。
 *
 * 必须带 observed_at:这是某一时刻的赔率快照,不是实时数据,不显示时间戳
 * 会让用户把一个可能是几小时前的数字当成当前赔率(CLAUDE.md §6.2 不伪装)。
 *
 * 2026-08-20 站长要求加交互:点击数字(主胜/平/客胜),该档数字放大、背景
 * 高亮,再点一次取消。这是纯展示态的本地选中,不发请求,各比赛卡之间互不
 * 影响(useState 组件内私有)。
 *
 * 2026-08-23 修复(P1):三段色块条不再是 <button>,只是纯展示的 <span>。
 * 此前色块条上也挂着 onClick+preventDefault+stopPropagation,而组件常被
 * 父级 <Link> 整行/整卡包住(MatchRow 整行、首页次要卡整张)——点色块这个
 * 视觉上最显眼的区域会把整行导航吞掉,页面看起来毫无反应。现在只有三个
 * 数字按钮保留点击高亮,并保留 preventDefault+stopPropagation 防止触发
 * 外层导航;色块条本身不拦截任何点击,点在色块上会正常触发外层 <Link>。
 * 容器 role 相应从 "img" 改成 "group"——里面确实有可交互的数字按钮,
 * role="img" 意味着不该有可聚焦子元素,原先自相矛盾。
 */

import { useState } from "react";
import type { WinProbability } from "@/lib/api-v1";
import styles from "./WinProbabilityBar.module.css";

type Outcome = "home" | "draw" | "away";

function pct(p: number): number {
  return Math.round(p * 100);
}

export function WinProbabilityBar({
  probability,
  compact = false,
  size = "sm",
}: {
  probability: WinProbability | null | undefined;
  /** 列表行里用更矮的条,首页重点卡用标准尺寸 */
  compact?: boolean;
  /** "lg":首页重点位免费卡专用——大号数字在上、小标签在下,取代
   * "标签 数字"同行的默认版式。和 compact 互斥,size="lg" 时忽略 compact。 */
  size?: "sm" | "lg";
}) {
  const [selected, setSelected] = useState<Outcome | null>(null);

  if (!probability) return null;
  const { p_home, p_draw, p_away } = probability;
  const home = pct(p_home);
  const draw = pct(p_draw);
  const away = pct(p_away);
  const label = `胜平负概率:主胜 ${home}%,平局 ${draw}%,客胜 ${away}%`;

  function toggle(outcome: Outcome, e: React.MouseEvent) {
    // 组件常被父级 <Link> 整体包住,点数字应该只是选中高亮,不能顺带触发
    // 整行导航——见模块顶部注释。色块条不再调用这个函数,不受影响。
    e.preventDefault();
    e.stopPropagation();
    setSelected((cur) => (cur === outcome ? null : outcome));
  }

  if (size === "lg") {
    return (
      <div className={styles.wrapLg} role="group" aria-label={label}>
        <div className={styles.barLg} aria-hidden="true">
          <span className={styles.segHome} style={{ width: `${p_home * 100}%` }} />
          <span className={styles.segDraw} style={{ width: `${p_draw * 100}%` }} />
          <span className={styles.segAway} style={{ width: `${p_away * 100}%` }} />
        </div>
        <div className={styles.numsLg}>
          <button
            type="button"
            className={styles.numGroup}
            data-selected={selected === "home"}
            aria-pressed={selected === "home"}
            onClick={(e) => toggle("home", e)}
          >
            <b className={`${styles.numHome} num`}>{home}%</b>
            <small>主胜</small>
          </button>
          <button
            type="button"
            className={styles.numGroup}
            data-selected={selected === "draw"}
            aria-pressed={selected === "draw"}
            onClick={(e) => toggle("draw", e)}
          >
            <b className={`${styles.numDraw} num`}>{draw}%</b>
            <small>平局</small>
          </button>
          <button
            type="button"
            className={styles.numGroup}
            data-selected={selected === "away"}
            aria-pressed={selected === "away"}
            onClick={(e) => toggle("away", e)}
          >
            <b className={`${styles.numAway} num`}>{away}%</b>
            <small>客胜</small>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className={compact ? styles.wrapCompact : styles.wrap}
      role="group"
      aria-label={label}
    >
      <div className={styles.bar} aria-hidden="true">
        <span className={styles.segHome} style={{ width: `${p_home * 100}%` }} />
        <span className={styles.segDraw} style={{ width: `${p_draw * 100}%` }} />
        <span className={styles.segAway} style={{ width: `${p_away * 100}%` }} />
      </div>
      <div className={styles.labels}>
        <button
          type="button"
          className={styles.labelHome}
          data-selected={selected === "home"}
          aria-pressed={selected === "home"}
          onClick={(e) => toggle("home", e)}
        >
          主胜 <b className="num">{home}%</b>
        </button>
        <button
          type="button"
          className={styles.labelDraw}
          data-selected={selected === "draw"}
          aria-pressed={selected === "draw"}
          onClick={(e) => toggle("draw", e)}
        >
          平 <b className="num">{draw}%</b>
        </button>
        <button
          type="button"
          className={styles.labelAway}
          data-selected={selected === "away"}
          aria-pressed={selected === "away"}
          onClick={(e) => toggle("away", e)}
        >
          客胜 <b className="num">{away}%</b>
        </button>
      </div>
    </div>
  );
}
