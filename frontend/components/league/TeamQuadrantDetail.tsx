"use client";

/**
 * 象限图详情面板(2026-09-09)。点击图上的队徽 / 下方名单后,显示该队在
 * **当前视角**下的数值与联赛内排名;选中两队时并排对比。
 *
 * 固定挂在图表下方的内联卡片,不做悬浮弹层(全站没有可复用的悬浮弹层组件,
 * 同 components/matches/ShotDetailPanel.tsx 的取舍);始终挂载,未选中时显示
 * 占位文案,避免首次选中时布局跳动。
 *
 * 只展示与当前视角相关的指标(两根轴 + view.related),不堆全部 17 项——
 * 每一项都带"第 N / 共 M 支",M 只数真正有该项数据的球队。
 * 缺值显示 "—" 并在下方说明是"数据源没给"还是"该队缺",不补 0。
 */

import { TeamBadge } from "@/components/teams/TeamBadge";
import type { TeamSeasonStatRow } from "@/lib/api-v1";
import {
  formatMetric,
  leagueMean,
  rankOf,
  teamKey,
  type MetricDef,
} from "./teamMetrics";
import { axisToMetric, quadrantOf, type Pt, type View } from "./quadrantViews";
import styles from "./TeamQuadrantDetail.module.css";

type Cell = {
  value: number | null;
  rank: { rank: number; total: number } | null;
  mean: { mean: number; n: number } | null;
};

function cellFor(rows: TeamSeasonStatRow[], row: TeamSeasonStatRow | undefined, m: MetricDef): Cell {
  const value = row ? m.value(row) : null;
  const key = row ? teamKey(row.team) : "";
  return {
    value,
    rank: row ? rankOf(rows, m, key) : null,
    mean: leagueMean(rows, m),
  };
}

function signed(v: number, digits: number) {
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}

export function TeamQuadrantDetail({
  view,
  rows,
  selected,
  mx,
  my,
  onRemove,
  onClear,
}: {
  view: View;
  rows: TeamSeasonStatRow[];
  /** 已选中的球队(0~2 支),顺序按选中先后 */
  selected: Pt[];
  mx: number;
  my: number;
  onRemove: (key: string) => void;
  onClear: () => void;
}) {
  if (!selected.length) {
    return (
      <div className={styles.panel} data-empty="true" aria-live="polite">
        <p className={styles.placeholder}>
          点击图上的队徽或下方名单查看该队数值与联赛排名，再点一支可对比。
        </p>
      </div>
    );
  }

  const lowY = view.y.lowerIsBetter === true;
  const metrics: MetricDef[] = [axisToMetric(view.x), axisToMetric(view.y), ...view.related];
  const rowOf = (p: Pt) => rows.find((r) => teamKey(r.team) === p.key);
  const teamRows = selected.map((p) => ({ pt: p, row: rowOf(p) }));
  const compare = selected.length === 2;

  const missingNotes = new Map<string, string>();
  const grid = metrics.map((m) => {
    const cells = teamRows.map(({ row }) => cellFor(rows, row, m));
    cells.forEach((cell, i) => {
      if (cell.value != null) return;
      const label = m.label + (m.perSeason ? "（赛季累计）" : "");
      if (!cell.mean) {
        missingNotes.set(m.id, `${label}：该联赛该赛季数据源未提供此项。`);
      } else {
        missingNotes.set(
          `${m.id}:${teamRows[i].pt.key}`,
          `${label}：${teamRows[i].pt.name}缺此项（联赛另有 ${cell.mean.n} 队有）。`,
        );
      }
    });
    return { m, cells };
  });

  return (
    <div className={styles.panel} aria-live="polite">
      <div className={styles.head}>
        <div className={styles.teams}>
          {teamRows.map(({ pt }) => (
            <div key={pt.key} className={styles.team}>
              <TeamBadge teamName={pt.name} crestUrl={pt.crestUrl} size={28} />
              <span className={styles.teamName}>{pt.name}</span>
              <span className={styles.quad}>{view.quadrants[quadrantOf(pt, mx, my, lowY)]}</span>
              <span className={styles.sample}>
                {pt.mp != null ? `样本 ${pt.mp} 场` : "样本场次未知"}
              </span>
              {compare && (
                <button
                  type="button"
                  className={styles.remove}
                  onClick={() => onRemove(pt.key)}
                  aria-label={`移除 ${pt.name}`}
                  title={`移除 ${pt.name}`}
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
        <button type="button" className={styles.clear} onClick={onClear}>
          清除选择
        </button>
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">{view.title}</th>
              {teamRows.map(({ pt }) => (
                <th key={pt.key} scope="col">
                  {pt.name}
                </th>
              ))}
              {compare && <th scope="col">差值</th>}
            </tr>
          </thead>
          <tbody>
            {grid.map(({ m, cells }) => {
              const label = m.label + (m.perSeason ? "（赛季累计）" : "");
              const [a, b] = cells;
              const diff = compare && a.value != null && b.value != null ? a.value - b.value : null;
              return (
                <tr key={m.id}>
                  <td>{label}</td>
                  {cells.map((cell, i) => {
                    if (cell.value == null) {
                      return (
                        <td key={teamRows[i].pt.key} className={styles.missing}>
                          —
                        </td>
                      );
                    }
                    const delta = cell.mean ? cell.value - cell.mean.mean : null;
                    const good = delta == null ? null : m.lowerIsBetter ? delta < 0 : delta > 0;
                    return (
                      <td key={teamRows[i].pt.key}>
                        <span className={styles.value}>{formatMetric(cell.value, m)}</span>
                        {cell.rank && (
                          <span className={styles.rank}>
                            第 {cell.rank.rank}/{cell.rank.total}
                          </span>
                        )}
                        {delta != null && cell.mean && (
                          <span
                            className={`${styles.delta} ${
                              delta === 0 ? "" : good ? styles.deltaGood : styles.deltaBad
                            }`}
                          >
                            均值 {formatMetric(cell.mean.mean, m)}（{signed(delta, m.digits)}）
                          </span>
                        )}
                      </td>
                    );
                  })}
                  {compare && (
                    <td className={diff == null ? styles.missing : styles.value}>
                      {diff == null ? "—" : signed(diff, m.digits)}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {missingNotes.size > 0 && (
        <ul className={styles.notes}>
          {[...missingNotes.values()].map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
      <p className={styles.foot}>
        「第 N/M」= 联赛内排名 / 有该项数据的球队数；排名与均值均为本联赛本赛季内部比较，不能跨联赛对比；
        {lowY ? `${view.y.label}越低越好，排名按升序。` : ""}
        {compare ? "差值 = 左队 − 右队。" : ""}
      </p>
    </div>
  );
}
