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
 * server component(纯展示,无交互状态),首页与 /matches 列表页共用。
 */

import type { WinProbability } from "@/lib/api-v1";
import styles from "./WinProbabilityBar.module.css";

function pct(p: number): number {
  return Math.round(p * 100);
}

export function WinProbabilityBar({
  probability,
  compact = false,
}: {
  probability: WinProbability | null | undefined;
  /** 列表行里用更矮的条,首页重点卡用标准尺寸 */
  compact?: boolean;
}) {
  if (!probability) return null;
  const { p_home, p_draw, p_away } = probability;
  const home = pct(p_home);
  const draw = pct(p_draw);
  const away = pct(p_away);

  return (
    <div
      className={compact ? styles.wrapCompact : styles.wrap}
      role="img"
      aria-label={`胜平负概率:主胜 ${home}%,平局 ${draw}%,客胜 ${away}%`}
    >
      <div className={styles.bar}>
        <span className={styles.segHome} style={{ width: `${p_home * 100}%` }} />
        <span className={styles.segDraw} style={{ width: `${p_draw * 100}%` }} />
        <span className={styles.segAway} style={{ width: `${p_away * 100}%` }} />
      </div>
      <div className={styles.labels}>
        <span className={styles.labelHome}>
          主胜 <b className="num">{home}%</b>
        </span>
        <span className={styles.labelDraw}>
          平 <b className="num">{draw}%</b>
        </span>
        <span className={styles.labelAway}>
          客胜 <b className="num">{away}%</b>
        </span>
      </div>
    </div>
  );
}
