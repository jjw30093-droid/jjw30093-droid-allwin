/**
 * 球员本场亮点句(playerHighlights.ts,2026-09-10)。
 *
 * 这套逻辑对照 FotMob 安卓包 236.17398 的 PlayerHighlight(POSITIVE/NEGATIVE +
 * priority + rankableCategory,文案有"独占/并列"两个变体)。它的危险之处在于
 * **凭空造出一句听起来很权威的话**,所以测试重点全在"什么时候不该出句":
 * 缺值不参与排名、池子太小不算冠军、数值太低不值一提。
 */

import { describe, expect, it } from "vitest";
import {
  MIN_POOL,
  buildPlayerHighlights,
  summarizePlayerShots,
  type PlayerStat,
} from "@/components/matches/playerHighlights";

function p(id: string, fields: Partial<PlayerStat> = {}): PlayerStat {
  return {
    player_id: id,
    name: `球员${id}`,
    team_id: 1,
    is_home: true,
    is_goalkeeper: false,
    ...fields,
  } as PlayerStat;
}

/** 造一个够大的池子(其余人都给一个低于目标的值)。 */
function pool(key: keyof PlayerStat, targetValue: number, others: number, n = MIN_POOL) {
  const target = p("me", { [key]: targetValue } as Partial<PlayerStat>);
  const rest = Array.from({ length: n - 1 }, (_, i) =>
    p(`o${i}`, { [key]: others } as Partial<PlayerStat>)
  );
  return { target, all: [target, ...rest] };
}

describe("排名类亮点", () => {
  it("全场最多时出句,并带上数值", () => {
    const { target, all } = pool("touches", 90, 30);
    const hs = buildPlayerHighlights(target, all);
    expect(hs.map((h) => h.text)).toContain("全场触球最多（90）");
  });

  it("并列时措辞不同(不能把并列说成独占)", () => {
    const { target, all } = pool("touches", 90, 30);
    all[1] = p("tie", { touches: 90 });
    const hs = buildPlayerHighlights(target, all);
    expect(hs.map((h) => h.text)).toContain("全场触球并列最多（90）");
  });

  it("不是最多就不出句", () => {
    const { target, all } = pool("touches", 90, 30);
    all.push(p("king", { touches: 120 }));
    expect(buildPlayerHighlights(target, all).map((h) => h.key)).not.toContain("touches");
  });

  it("池子不足 MIN_POOL 时不算冠军(体能数据只有个别联赛有)", () => {
    const target = p("me", { physical_metrics_distance_covered: 11000 });
    const all = [target, p("o1", { physical_metrics_distance_covered: 9000 })];
    expect(buildPlayerHighlights(target, all)).toEqual([]);
  });

  it("缺失值不参与排名,也不被当成 0", () => {
    // 目标球员该字段缺失 → 不该因为"别人都是 0/缺失"而夺冠
    const target = p("me", {});
    const all = [target, ...Array.from({ length: MIN_POOL }, (_, i) => p(`o${i}`, { touches: 10 }))];
    expect(buildPlayerHighlights(target, all).map((h) => h.key)).not.toContain("touches");
  });

  it("数值太低时即使全场最多也不出句", () => {
    const { target, all } = pool("fouls", 1, 0);
    expect(buildPlayerHighlights(target, all)).toEqual([]);
  });

  it("未完赛用「目前」,完赛用「全场」", () => {
    const { target, all } = pool("touches", 90, 30);
    expect(buildPlayerHighlights(target, all, { finished: false })[0].text).toContain("目前");
    expect(buildPlayerHighlights(target, all, { finished: true })[0].text).toContain("全场");
  });

  it("负面项标记为 negative", () => {
    const { target, all } = pool("dispossessed", 8, 1);
    const h = buildPlayerHighlights(target, all).find((x) => x.key === "dispossessed");
    expect(h?.tone).toBe("negative");
  });

  it("最多只出 3 条,且按优先级排序(事件项优先于普通排名项)", () => {
    const { target, all } = pool("touches", 90, 30);
    Object.assign(target, {
      accurate_passes: 80, chances_created: 5, recoveries: 12, errors_led_to_goal: 1,
    });
    for (const o of all.slice(1)) {
      Object.assign(o, { accurate_passes: 20, chances_created: 0, recoveries: 2 });
    }
    const hs = buildPlayerHighlights(target, all);
    expect(hs).toHaveLength(3);
    expect(hs[0].key).toBe("errors_led_to_goal");
  });
});

describe("事件类亮点", () => {
  it("发生即播报,不需要池子(来源只发非零项,池子永远凑不够)", () => {
    const target = p("me", { penalties_won: 1 });
    expect(buildPlayerHighlights(target, [target]).map((h) => h.text)).toEqual(["赢得点球"]);
  });

  it("次数大于 1 时带上次数", () => {
    const target = p("me", { shots_woodwork: 2 });
    expect(buildPlayerHighlights(target, [target])[0].text).toBe("击中门框 2 次");
  });

  it("没有值时什么都不说", () => {
    const target = p("me", {});
    expect(buildPlayerHighlights(target, [target])).toEqual([]);
  });
});

describe("summarizePlayerShots", () => {
  type Shot = Parameters<typeof summarizePlayerShots>[0][number];
  const shot = (o: Partial<Shot>): Shot => ({ player_id: "me", ...o }) as Shot;
  const me = (o: Partial<PlayerStat> = {}) => p("me", o);

  it("只统计该球员的射门,随 player_id 变化", () => {
    const shots = [
      shot({ outcome: "Goal", xg: 0.3 }),
      shot({ outcome: "Miss", xg: 0.1 }),
      shot({ player_id: "other", outcome: "Goal", xg: 0.9 }),
    ];
    const mine = summarizePlayerShots(shots, me({ shots_on_target: 1 }))!;
    expect(mine.shots).toBe(2);
    expect(mine.xgTotal).toBeCloseTo(0.4, 6);
    expect(summarizePlayerShots(shots, p("other", { shots_on_target: 1 }))!.shots).toBe(1);
  });

  it("射正取官方球员统计,不从射门图逐脚推", () => {
    // 逐脚推(进球 + 未被封堵的 AttemptSaved)会得到 2;官方说 1,就显示 1。
    // 生产实测:Is_Blocked 未回填的比赛里逐脚推只有 61.1% 与官方一致,
    // 系统性高估(把被后卫封堵的球算成射正),所以这里只认官方值。
    const shots = [
      shot({ outcome: "Goal" }),
      shot({ outcome: "AttemptSaved", is_blocked: null }),
      shot({ outcome: "Miss" }),
    ];
    expect(summarizePlayerShots(shots, me({ shots_on_target: 1 }))!.onTarget).toBe(1);
  });

  it("官方射正缺失时为 null,不退回逐脚推、也不当 0", () => {
    const shots = [shot({ outcome: "Goal" }), shot({ outcome: "AttemptSaved" })];
    expect(summarizePlayerShots(shots, me({}))!.onTarget).toBeNull();
    expect(summarizePlayerShots(shots, me({ shots_on_target: null }))!.onTarget).toBeNull();
  });

  it("官方射正为 0 时如实显示 0,与缺失区分开", () => {
    expect(summarizePlayerShots([shot({ outcome: "Miss" })], me({ shots_on_target: 0 }))!.onTarget).toBe(0);
  });

  it("没有射门时返回 null(不返回一堆 0)", () => {
    expect(summarizePlayerShots([], me())).toBeNull();
    expect(summarizePlayerShots([shot({ player_id: "x" })], me())).toBeNull();
  });

  it("缺失 xG 不当 0 累加,而是如实缩小分母", () => {
    const shots = [shot({ xg: 0.5 }), shot({ xg: null }), shot({})];
    const s = summarizePlayerShots(shots, me())!;
    expect(s.shots).toBe(3);
    expect(s.xgCounted).toBe(1);
    expect(s.xgTotal).toBeCloseTo(0.5, 6);
  });

  it("全部射门都没有 xG 时 xgTotal 为 null,不是 0", () => {
    const s = summarizePlayerShots([shot({ xg: null }), shot({})], me())!;
    expect(s.xgTotal).toBeNull();
    expect(s.xgCounted).toBe(0);
  });
});
