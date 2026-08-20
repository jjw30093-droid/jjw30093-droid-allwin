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
 * 2026-08-20 站长要求加交互:点击某一档(主胜/平/客胜)的色块或数字,该档
 * 数字放大、色块高亮,再点一次取消。这是纯展示态的本地选中,不发请求、不
 * 影响其它比赛卡各自独立(useState 组件内私有)。整条组件常被父级 <Link>
 * 包住(MatchRow 整行是链接、首页次要卡整张是链接)——点击必须
 * preventDefault+stopPropagation,否则点一下选中态还没看到就跳转到详情页
 * 了;size="lg" 用在纯 <article>(首页重点卡)里没有外层链接,同样调用这两个
 * 方法是安全的空操作,不需要按用法分支两套实现。
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
    // 组件常被父级 <Link> 整体包住,点色块/数字应该只是选中高亮,不能顺带
    // 触发整行导航——见模块顶部注释。
    e.preventDefault();
    e.stopPropagation();
    setSelected((cur) => (cur === outcome ? null : outcome));
  }

  if (size === "lg") {
    return (
      <div className={styles.wrapLg} role="img" aria-label={label}>
        <div className={styles.barLg}>
          <button
            type="button"
            className={styles.segHome}
            style={{ width: `${p_home * 100}%` }}
            data-selected={selected === "home"}
            aria-pressed={selected === "home"}
            aria-label={`主胜概率条 ${home}%`}
            onClick={(e) => toggle("home", e)}
          />
          <button
            type="button"
            className={styles.segDraw}
            style={{ width: `${p_draw * 100}%` }}
            data-selected={selected === "draw"}
            aria-pressed={selected === "draw"}
            aria-label={`平局概率条 ${draw}%`}
            onClick={(e) => toggle("draw", e)}
          />
          <button
            type="button"
            className={styles.segAway}
            style={{ width: `${p_away * 100}%` }}
            data-selected={selected === "away"}
            aria-pressed={selected === "away"}
            aria-label={`客胜概率条 ${away}%`}
            onClick={(e) => toggle("away", e)}
          />
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
      role="img"
      aria-label={label}
    >
      <div className={styles.bar}>
        <button
          type="button"
          className={styles.segHome}
          style={{ width: `${p_home * 100}%` }}
          data-selected={selected === "home"}
          aria-pressed={selected === "home"}
          aria-label={`主胜概率条 ${home}%`}
          onClick={(e) => toggle("home", e)}
        />
        <button
          type="button"
          className={styles.segDraw}
          style={{ width: `${p_draw * 100}%` }}
          data-selected={selected === "draw"}
          aria-pressed={selected === "draw"}
          aria-label={`平局概率条 ${draw}%`}
          onClick={(e) => toggle("draw", e)}
        />
        <button
          type="button"
          className={styles.segAway}
          style={{ width: `${p_away * 100}%` }}
          data-selected={selected === "away"}
          aria-pressed={selected === "away"}
          aria-label={`客胜概率条 ${away}%`}
          onClick={(e) => toggle("away", e)}
        />
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
