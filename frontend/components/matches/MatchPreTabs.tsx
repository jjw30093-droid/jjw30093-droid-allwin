/**
 * 比赛详情页赛前/进行中内容 —— 单栏纵向布局(2026-08-23 重排,站长批准
 * 偏离 CLAUDE.md §11.1 锁定顺序与 2026-08-14 两层 tab 定稿)。
 *
 * 废弃背景:原来是"看点/数据/赔率"三个横向 tab,赔率(竞彩用户最关心的
 * 内容)被埋在第三个 tab 里,球队数据要点两次才能到达;没有已发布精选的
 * 场次,"看点" tab 又只剩一个空标题。改成纵向铺开 + 吸顶锚点导航条后,
 * 内容一项不减,只是重新排序为:赔率 → 盘口参考 → 同期事件(不一定每场
 * 都有,不进导航条)→ 球队数据 → 数据说明。
 *
 * 不再用 role="tablist"/hidden 隐藏未选中面板——所有内容一直在 DOM 里,
 * 纵向可滚动,锚点链接只是原生 <a href="#id"> + CSS scroll-behavior,
 * 不需要 useState,也就不需要 "use client"。
 */

import type { ReactNode } from "react";
import styles from "./MatchPreTabs.module.css";

const ANCHORS = [
  { id: "pre-odds", label: "赔率" },
  { id: "pre-market", label: "盘口" },
  { id: "pre-data", label: "数据" },
  { id: "pre-notes", label: "说明" },
];

export function MatchPreTabs({
  matchInfo,
  odds,
  market,
  events,
  data,
  notes,
}: {
  /** 比赛信息卡 + 本场看点(推荐发布状态),不进锚点导航。 */
  matchInfo: ReactNode;
  odds: ReactNode;
  market: ReactNode;
  /** 同期事件(关键变化)——组件自带标题、无内容时整体不渲染,不一定
   * 每场都有,因此不单独列进锚点导航,只在赔率和盘口参考之间自然出现。 */
  events: ReactNode;
  data: ReactNode;
  notes: ReactNode;
}) {
  return (
    <div className={styles.wrap}>
      {matchInfo}
      <nav className={styles.anchorNav} aria-label="快速跳转">
        {ANCHORS.map((a) => (
          <a key={a.id} href={`#${a.id}`}>
            {a.label}
          </a>
        ))}
      </nav>
      <div id="pre-odds" className={styles.jumpTarget}>
        {odds}
      </div>
      <div id="pre-market" className={styles.jumpTarget}>
        {market}
      </div>
      {events}
      <div id="pre-data" className={styles.jumpTarget}>
        {data}
      </div>
      <div id="pre-notes" className={styles.jumpTarget}>
        {notes}
      </div>
    </div>
  );
}
