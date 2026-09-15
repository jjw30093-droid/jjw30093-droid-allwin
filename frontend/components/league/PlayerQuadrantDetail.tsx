"use client";

/**
 * 球员象限图详情面板(2026-09-15)。对标 TeamQuadrantDetail.tsx——点击图上
 * 的头像/下方名单后,显示该球员在**当前视角**下的数值与位置内排名;选中
 * 两人时并排对比。排名/均值只统计"当前选中位置"内画在图上的球员——与
 * 球队版同一个理由(虚线代表的均值必须和面板数字对得上)。
 */

import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import type { PlayerQuadrantRow } from "@/lib/api-v1";
import { formatMetric, leagueMean, playerKey, rankOf, type PlayerMetricDef } from "./playerMetrics";
import { dirsOf, quadrantOf, type PlayerPt, type PlayerView } from "./playerQuadrantViews";
import styles from "./TeamQuadrantDetail.module.css";

type Cell = {
  value: number | null;
  rank: { rank: number; total: number } | null;
  mean: { mean: number; n: number } | null;
};

function cellFor(rows: PlayerQuadrantRow[], row: PlayerQuadrantRow | undefined, m: PlayerMetricDef): Cell {
  const value = row ? m.value(row) : null;
  const key = row ? playerKey(row.player) : "";
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

export function PlayerQuadrantDetail({
  view,
  rows,
  selected,
  mx,
  my,
  onRemove,
  onClear,
}: {
  view: PlayerView;
  /** 当前选中位置内的全部行(不是已画出的 10 人)——排名/均值统计范围。 */
  rows: PlayerQuadrantRow[];
  selected: PlayerPt[];
  mx: number;
  my: number;
  onRemove: (key: string) => void;
  onClear: () => void;
}) {
  if (!selected.length) {
    return (
      <div className={styles.panel} data-empty="true" aria-live="polite">
        <p className={styles.placeholder}>
          点击图上的头像或下方名单查看该球员数值与位置内排名，再点一人可对比。
        </p>
      </div>
    );
  }

  // 2026-09-16 真实事故修正:这里此前只记了 view.y.lowerIsBetter(lowY)
  // 一个布尔值传给 quadrantOf,x 轴的方向被悄悄丢掉——"门将"视角的 x 轴
  // (每90分钟面对射正预期进球)恰好是 lowerIsBetter,导致详情面板算出的
  // 象限标签和图表本身的图例分组(用完整 dirsOf(view) 算的)对不上,
  // 同一个球员在两处显示成不同的象限。用 dirsOf(view) 同时带上 x/y 两个
  // 方向,是 quadrantViews.ts::dirsOf 的既有设计意图(见其函数头注释),
  // 之前只是没有真的接上。
  const dirs = dirsOf(view);
  const lowX = dirs.x;
  const lowY = dirs.y;
  const metrics: PlayerMetricDef[] = [view.x, view.y];
  const rowOf = (p: PlayerPt) => rows.find((r) => playerKey(r.player) === p.key);
  const playerRows = selected.map((p) => ({ pt: p, row: rowOf(p) }));
  const compare = selected.length === 2;

  const missingNotes = new Map<string, string>();
  const grid = metrics.map((m) => {
    const cells = playerRows.map(({ row }) => cellFor(rows, row, m));
    cells.forEach((cell, i) => {
      if (cell.value != null) return;
      if (!cell.mean) {
        missingNotes.set(m.id, `${m.label}：该位置数据源未提供此项。`);
      } else {
        missingNotes.set(
          `${m.id}:${playerRows[i].pt.key}`,
          `${m.label}：${playerRows[i].pt.name}缺此项（该位置另有 ${cell.mean.n} 人有）。`,
        );
      }
    });
    return { m, cells };
  });

  return (
    <div className={styles.panel} aria-live="polite">
      <div className={styles.head}>
        <div className={styles.teams}>
          {playerRows.map(({ pt, row }) => (
            <div key={pt.key} className={styles.team}>
              <PlayerAvatar playerId={pt.playerId ?? pt.key} playerName={pt.name} size={28} />
              <span className={styles.teamName}>{pt.name}</span>
              <span className={styles.quad}>{view.quadrants[quadrantOf(pt, mx, my, dirs)]}</span>
              <span className={styles.sample}>
                {pt.mp != null ? (
                  <>
                    样本 <span className="num">{pt.mp}</span> 场
                  </>
                ) : (
                  "样本场次未知"
                )}
              </span>
              {row && row.teams_count != null && row.teams_count > 1 && (
                <span className={styles.sample} title="该球员本赛季效力过多支球队,出场门槛取更严格的那支队的已踢时间">
                  转会球员
                </span>
              )}
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
              {playerRows.map(({ pt }) => (
                <th key={pt.key} scope="col">
                  {pt.name}
                </th>
              ))}
              {compare && <th scope="col">差值</th>}
            </tr>
          </thead>
          <tbody>
            {grid.map(({ m, cells }) => {
              const [a, b] = cells;
              const diff = compare && a.value != null && b.value != null ? a.value - b.value : null;
              return (
                <tr key={m.id}>
                  <td>{m.label}</td>
                  {cells.map((cell, i) => {
                    if (cell.value == null) {
                      return (
                        <td key={playerRows[i].pt.key} className={styles.missing}>
                          —
                        </td>
                      );
                    }
                    const delta = cell.mean ? cell.value - cell.mean.mean : null;
                    const good = delta == null ? null : m.lowerIsBetter ? delta < 0 : delta > 0;
                    return (
                      <td key={playerRows[i].pt.key}>
                        <span className={`${styles.value} num`}>{formatMetric(cell.value, m)}</span>
                        {cell.rank && (
                          <span className={`${styles.rank} num`}>
                            第 {cell.rank.rank}/{cell.rank.total}
                          </span>
                        )}
                        {delta != null && cell.mean && (
                          <span
                            className={`${styles.delta} num ${
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
                    <td className={diff == null ? styles.missing : `${styles.value} num`}>
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
        「第 N/M」= 该位置内排名 / 有该项数据的球员数；排名与均值只统计画在图上、当前选中位置内达标的球员,均为本联赛本赛季内部比较,不能跨位置或跨联赛对比;
        {lowX ? `${view.x.label}越低越好,排名按升序。` : ""}
        {lowY ? `${view.y.label}越低越好,排名按升序。` : ""}
        {compare ? "差值 = 左人 − 右人。" : ""}
      </p>
      {metrics.some((m) => m.caliber) && (
        <ul className={styles.notes}>
          {metrics
            .filter((m) => m.caliber)
            .map((m) => (
              <li key={`caliber:${m.id}`}>
                {m.label}：{m.caliber}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
