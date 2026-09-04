/**
 * 首页战绩 banner 文案组装(frontend/lib/reco-highlight.ts)。
 *
 * 2026-09 站长要求 banner 只留一行(去掉细行与 CTA)。这件事顺带把展示口径
 * 变得更保守:**百分比整体消失,主行以原始计数「N 单 M 中」为主体**。
 *
 * 所以这里守两条:
 * 1. 每条文案都必须含原始计数——这是"实事求是"在展示层唯一的落点;
 * 2. 不得出现裸百分比(有 % 就必须同时有计数)。这条现在恒真(没有 % 了),
 *    但留着,因为它是防止后人把百分比加回来却不带计数的那道闸。
 *
 * CLAUDE.md 里对应的条款已按站长决定删除,所以这个测试就是唯一的守卫了。
 */

import { describe, expect, it } from "vitest";
import { highlightLines, type BoardHighlight } from "@/lib/reco-highlight";

/** 原始计数的形态:「5 单 5 中」/「18 单 15 中」/「近 5 单全中」。 */
const COUNT_RE = /\d+\s*单\s*\d+\s*中|近\s*\d+\s*单全中|\(\s*\d+\s*\/\s*\d+\s*\)/;

function base(overrides: Partial<BoardHighlight> = {}): BoardHighlight {
  return {
    board: "daily_pick",
    board_label_zh: "每日精选",
    kind: "empty",
    streak: null,
    window: null,
    segment: null,
    rate: null,
    parlay_slip_count: null,
    parlay_net_units: null,
    candidate_key: null,
    candidates_considered: 0,
    ...overrides,
  } as BoardHighlight;
}

const WINDOW_30 = {
  kind: "days" as const,
  value: 30,
  observed_from_date: "2026-08-14",
  observed_to_date: "2026-09-02",
};

function rate(over: Partial<NonNullable<BoardHighlight["rate"]>> = {}) {
  return {
    unit: "slip" as const,
    decided_count: 18,
    win_count: 15,
    lose_count: 3,
    half_win_count: 0,
    half_loss_count: 0,
    push_count: 1,
    hit_rate: 0.8333,
    ...over,
  };
}

const ALL_FORMS: BoardHighlight[] = [
  base({ kind: "streak", streak: { length: 5, unit: "slip", skipped_push_count: 0,
         skipped_void_count: 0, from_date: "2026-09-01", to_date: "2026-09-02" } }),
  base({ kind: "rate_qualified", window: WINDOW_30, rate: rate(),
         segment: { kind: "overall", market: null, league_id: null, league_name_zh: null } }),
  base({ kind: "rate_qualified", window: WINDOW_30,
         rate: rate({ decided_count: 5, win_count: 5, lose_count: 0, push_count: 0, hit_rate: 1.0 }),
         segment: { kind: "market", market: "ou", league_id: null, league_name_zh: null } }),
  base({ kind: "rate_best_effort", window: WINDOW_30,
         rate: rate({ decided_count: 13, win_count: 10, lose_count: 3, hit_rate: 0.7692 }),
         segment: { kind: "market", market: "ah", league_id: null, league_name_zh: null } }),
  // n=1(站长明确拒绝样本量下限)——正因为没有下限,「1 单 1 中」必须写出来,
  // 读者才能自己判断这个战绩的分量。
  base({ kind: "rate_qualified", window: WINDOW_30,
         rate: rate({ decided_count: 1, win_count: 1, lose_count: 0, push_count: 0, hit_rate: 1.0 }),
         segment: { kind: "overall", market: null, league_id: null, league_name_zh: null } }),
  base({ kind: "parlay_return", window: WINDOW_30, parlay_slip_count: 1, parlay_net_units: 2.66 }),
];

describe("文案必含原始计数(实事求是在展示层的唯一落点)", () => {
  it.each(ALL_FORMS.filter((h) => h.kind !== "parlay_return").map((h, i) => [i, h] as const))(
    "case %i:主行含 N 单 M 中 形态的计数",
    (_i, h) => {
      expect(highlightLines(h)!.main).toMatch(COUNT_RE);
    },
  );

  it.each(ALL_FORMS.map((h, i) => [i, h] as const))(
    "case %i:不得出现裸百分比(有 %% 必有计数)",
    (_i, h) => {
      const main = highlightLines(h)!.main;
      if (main.includes("%")) expect(main).toMatch(COUNT_RE);
    },
  );

  it("裸百分比会被 COUNT_RE 抓住(反向验证正则不是恒真)", () => {
    expect("每日精选 · 命中率 100%").not.toMatch(COUNT_RE);
    expect("每日精选 · 18 单 15 中").toMatch(COUNT_RE);
    expect("每日精选 · 近 5 单全中").toMatch(COUNT_RE);
  });
});

describe("横条拆分:boardShort / value 与 main 不得漂移", () => {
  // 2026-09 横条改版把一行文案拆成"灰色板块前缀 + 彩色口径与计数"两段渲染。
  // main 保留下来当不变量的断言对象(上面那两条"必含原始计数"就是断言它),
  // 所以两者一旦漂移,守卫就会守着一个页面上并不存在的字符串。
  it.each(ALL_FORMS.map((h, i) => [i, h] as const))(
    "case %i:main === `${板块} · ${value}`",
    (_i, h) => {
      const l = highlightLines(h)!;
      expect(l.main).toBe(`${h.board_label_zh} · ${l.value}`);
    },
  );

  it("boardShort 脱掉「每日」前缀——一行里出现两次纯属噪音", () => {
    const h = base({ kind: "streak", streak: { length: 5, unit: "slip",
      skipped_push_count: 0, skipped_void_count: 0,
      from_date: "2026-09-01", to_date: "2026-09-02" } });
    const l = highlightLines(h)!;
    expect(l.boardShort).toBe("精选");
    expect(l.value).toBe("近 5 单全中");
  });

  it("未知板块标签原样返回,不盲切前两字(切错比长一点更糟)", () => {
    const h = base({ board_label_zh: "站长特选", kind: "streak",
      streak: { length: 3, unit: "slip", skipped_push_count: 0,
                skipped_void_count: 0, from_date: "2026-09-01",
                to_date: "2026-09-02" } });
    expect(highlightLines(h)!.boardShort).toBe("站长特选");
  });

  it("value 同样必含原始计数(横条上真正被渲染的是它,不是 main)", () => {
    for (const h of ALL_FORMS.filter((x) => x.kind !== "parlay_return")) {
      expect(highlightLines(h)!.value).toMatch(COUNT_RE);
    }
  });
});

describe("连中", () => {
  it("正常情形只有一行,不带任何附注", () => {
    const h = base({ kind: "streak", streak: { length: 5, unit: "slip",
      skipped_push_count: 0, skipped_void_count: 0,
      from_date: "2026-09-01", to_date: "2026-09-02" } });
    const l = highlightLines(h)!;
    expect(l.main).toBe("每日精选 · 近 5 单全中");
    expect(l.emphasize).toBe(true);
  });

  it("连中**其间**确实跳过了走水时仍然披露(否则「全中」失真)", () => {
    const h = base({ kind: "streak", streak: { length: 5, unit: "slip",
      skipped_push_count: 1, skipped_void_count: 0,
      from_date: "2026-09-01", to_date: "2026-09-02" } });
    expect(highlightLines(h)!.main).toContain("其间 1 单走水不计");
  });

  it("作废同样披露", () => {
    const h = base({ kind: "streak", streak: { length: 4, unit: "slip",
      skipped_push_count: 0, skipped_void_count: 2,
      from_date: "2026-09-01", to_date: "2026-09-02" } });
    expect(highlightLines(h)!.main).toContain("2 单作废");
  });
});

describe("分段文案", () => {
  it("market 走 MARKET_ZH 映射", () => {
    const h = base({ kind: "rate_qualified", window: WINDOW_30, rate: rate(),
      segment: { kind: "market", market: "ou", league_id: null, league_name_zh: null } });
    expect(highlightLines(h)!.main).toContain("大小球");
  });

  it("未知 market 原样显示,不崩(?? 兜底)", () => {
    const h = base({ kind: "rate_qualified", window: WINDOW_30, rate: rate(),
      segment: { kind: "market", market: "btts", league_id: null, league_name_zh: null } });
    expect(highlightLines(h)!.main).toContain("btts");
  });

  it("联赛×市场同时展示", () => {
    const h = base({ kind: "rate_qualified", window: WINDOW_30, rate: rate(),
      segment: { kind: "league_market", market: "ah", league_id: 87, league_name_zh: "西甲" } });
    const main = highlightLines(h)!.main;
    expect(main).toContain("西甲");
    expect(main).toContain("亚洲让球");
  });
});

describe("其它形态", () => {
  it("empty 不渲染", () => {
    expect(highlightLines(base({ kind: "empty" }))).toBeNull();
  });

  it("串关走回报口径,不出现命中率", () => {
    const h = base({ kind: "parlay_return", window: WINDOW_30,
      parlay_slip_count: 1, parlay_net_units: 2.66 });
    const l = highlightLines(h)!;
    expect(l.main).toContain("串关 1 单");
    expect(l.main).toContain("+2.66 单位");
    expect(l.main).not.toContain("命中率");
  });

  it("不达标的命中率不加强调", () => {
    const h = base({ kind: "rate_best_effort", window: WINDOW_30,
      rate: rate({ decided_count: 10, win_count: 4, hit_rate: 0.4 }),
      segment: { kind: "overall", market: null, league_id: null, league_name_zh: null } });
    expect(highlightLines(h)!.emphasize).toBe(false);
  });
});

describe("禁用词哨兵(品牌文档黑名单仍然有效)", () => {
  it("任何形态的文案都不含连红/必胜/稳赚/红单", () => {
    for (const h of ALL_FORMS) {
      const main = highlightLines(h)!.main;
      for (const banned of ["连红", "必胜", "稳赚", "红单", "精准命中"]) {
        expect(main).not.toContain(banned);
      }
    }
  });
});
