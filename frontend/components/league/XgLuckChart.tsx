"use client";

/**
 * xG 运气榜 —— 发散条形图,零线居中:右侧金色 = 比"按 xG 应得"多拿分,
 * 左侧红色 = 少拿分。
 *
 * 数据来自 fact_league_table 的 `table_type='xg'`(695 行),此前 **100% 不可见**
 * —— queries/matches.standings 硬编码 `table_type='all'`,这一档连 API 都出不去。
 * `x_points_diff` 是 extra_json 里预算好的 xPointsDiff,实测确认口径就是
 * **实际积分 − 按 xG 应得积分**(阿森纳 85 实得 vs 77.8 应得 = +7.18)。
 *
 * 名次说明:xg 档行里的 `position` 是**按 xG 推导的名次**,不是实际名次;
 * 实际名次 = x_position + x_position_diff(已用真实 all 榜逐队核对:
 * 维拉 13+(-9)=4、桑德兰 18+(-11)=7、曼联 3+0=3,全部吻合)。
 *
 * 这是 FotMob 官方 xG 口径,**不是本站模型** —— 文案上必须说清来源,
 * 否则会被读成"你们模型算的"。
 */

import type { EChartsOption } from "echarts";
import { EChart } from "@/components/EChart";
import type { StandingsResponse } from "@/lib/api-v1";
import styles from "./XgLuckChart.module.css";

type Row = StandingsResponse["rows"][number];

const GOLD = "#d49e33";
const LOSS = "#c05437";
const INK2 = "#a79c87";

type LuckRow = {
  name: string;
  diff: number;
  xPosition: number | null;
  actualPosition: number | null;
};

export function buildLuckRows(rows: Row[]): LuckRow[] {
  return rows
    .filter((r) => typeof r.x_points_diff === "number")
    .map((r) => {
      const xPos = typeof r.x_position === "number" ? r.x_position : null;
      const posDiff =
        typeof r.x_position_diff === "number" ? r.x_position_diff : null;
      return {
        name: r.team.name,
        diff: r.x_points_diff as number,
        xPosition: xPos,
        // 实际名次由 xG 名次 + 名次差反推(xg 档的 position 列是 xG 名次)
        actualPosition: xPos != null && posDiff != null ? xPos + posDiff : null,
      };
    })
    .sort((a, b) => b.diff - a.diff);
}

export function XgLuckChart({ rows }: { rows: Row[] }) {
  const data = buildLuckRows(rows);
  if (data.length === 0) {
    return (
      <p className={styles.empty}>
        该联赛该赛季暂无 xG 榜数据(该档由数据源提供,并非每个联赛都有)。
      </p>
    );
  }

  const top = data[0];
  const bottom = data[data.length - 1];
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.diff)), 1);

  const option: EChartsOption = {
    grid: { left: 88, right: 64, top: 8, bottom: 24 },
    xAxis: {
      type: "value",
      min: -maxAbs * 1.15,
      max: maxAbs * 1.15,
      axisLabel: {
        color: INK2,
        fontSize: 11,
        formatter: (v: number) => (v === 0 ? "0" : `${v > 0 ? "+" : ""}${v.toFixed(0)}`),
      },
      splitLine: { lineStyle: { opacity: 0.12 } },
    },
    yAxis: {
      type: "category",
      // 从上到下:多拿分 → 少拿分
      data: [...data].reverse().map((d) => d.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: INK2, fontSize: 11 },
    },
    tooltip: {
      confine: true,
      trigger: "item",
      formatter: (p: unknown) => {
        const { dataIndex } = p as { dataIndex: number };
        const d = [...data].reverse()[dataIndex];
        const posLine =
          d.actualPosition != null && d.xPosition != null
            ? `<br/>实际第 ${d.actualPosition} 名 · 按 xG 应在第 ${d.xPosition} 名`
            : "";
        return `${d.name}<br/>${d.diff > 0 ? "多拿" : "少拿"} ${Math.abs(d.diff).toFixed(1)} 分${posLine}`;
      },
    },
    series: [
      {
        type: "bar",
        data: [...data].reverse().map((d) => ({
          value: Number(d.diff.toFixed(2)),
          itemStyle: { color: d.diff >= 0 ? GOLD : LOSS },
        })),
        barWidth: "62%",
        label: {
          show: true,
          position: "right",
          color: INK2,
          fontSize: 11,
          formatter: ({ value }: { value: unknown }) => {
            const v = Number(value);
            return `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
          },
        },
      },
    ],
  };

  const summary =
    `xG 运气榜(FotMob 官方 xG 口径,非本站模型):正值表示实际积分高于按 xG 应得的积分。` +
    `${top.name} 多拿 ${top.diff.toFixed(1)} 分` +
    (top.actualPosition != null && top.xPosition != null
      ? `(实际第 ${top.actualPosition},按 xG 应在第 ${top.xPosition})`
      : "") +
    `;${bottom.name} 少拿 ${Math.abs(bottom.diff).toFixed(1)} 分` +
    (bottom.actualPosition != null && bottom.xPosition != null
      ? `(实际第 ${bottom.actualPosition},按 xG 应在第 ${bottom.xPosition})`
      : "") +
    `。xG 只衡量射门机会质量,不解释扑救、门框与临门一脚,差值大不等于"必然回归"。`;

  return (
    <div className={styles.wrap}>
      <p className={styles.lede}>
        {top.name} 比按 xG 应得的多拿了 <b className="num">{top.diff.toFixed(1)}</b> 分
        {top.actualPosition != null && top.xPosition != null && (
          <>
            （实际第 {top.actualPosition} 名，按 xG 应在第 {top.xPosition} 名）
          </>
        )}
        ；{bottom.name} 少拿 <b className="num">{Math.abs(bottom.diff).toFixed(1)}</b> 分。
      </p>
      <EChart option={option} height={Math.max(280, data.length * 26)} ariaSummary={summary} />
      <p className={styles.note}>
        口径:实际积分 − 按 xG 应得积分。xG 与预期积分均来自数据源(FotMob)的官方
        xG 榜，不是本站模型输出。xG 只衡量射门机会质量，不包含扑救、门框与
        临门一脚的差别，数值大不代表「接下来一定回归」。
      </p>
    </div>
  );
}
