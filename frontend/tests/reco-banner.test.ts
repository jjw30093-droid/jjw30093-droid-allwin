/**
 * 首页公推 banner 的纯判定逻辑(frontend/lib/reco-banner.ts)。
 *
 * 这套判定刻意放在前端:后端 /reco/public/current 走共享缓存、首页又是 ISR,
 * 服务端算出的"该不该显示"会随缓存变陈旧;后端只下发 kickoff 事实,由客户端
 * 按自己的当前时间算。函数一律接收 now 参数、不读时钟,所以边界值可精确测。
 *
 * 注意:「已结算/作废立刻撤下」**不在本文件测**——那是后端
 * status='published' 的过滤,归 tests/backend/test_reco_board.py::
 * TestPublicCurrentEndpoint。这里只测"时间到了没"。
 */

import { describe, expect, it } from "vitest";
import {
  comboLabel,
  pickHideAtMs,
  visiblePublicPicks,
  type PickTiming,
} from "@/lib/reco-banner";

const HOUR = 3600_000;
const T = Date.parse("2027-04-01T12:00:00Z");

function slip(...kickoffs: (string | null)[]): PickTiming {
  return { legs: kickoffs.map((k) => ({ kickoff_at_utc: k })) };
}

describe("pickHideAtMs", () => {
  it("单关:返回开球时刻 + hideAfterHours(精确毫秒)", () => {
    expect(pickHideAtMs(slip("2027-04-01T12:00:00Z"), 2)).toBe(T + 2 * HOUR);
  });

  it("串关:取最后一场开球,不是最早、也不是第一条", () => {
    const s = slip(
      "2027-04-01T12:00:00Z",
      "2027-04-01T15:00:00Z",
      "2027-04-01T13:00:00Z",
    );
    expect(pickHideAtMs(s, 2)).toBe(T + 3 * HOUR + 2 * HOUR);
  });

  it("串关腿乱序时结果相同(不依赖数组顺序)", () => {
    const ordered = slip("2027-04-01T12:00:00Z", "2027-04-01T15:00:00Z");
    const shuffled = slip("2027-04-01T15:00:00Z", "2027-04-01T12:00:00Z");
    expect(pickHideAtMs(shuffled, 2)).toBe(pickHideAtMs(ordered, 2));
  });

  it("任意一条腿缺开球时间 → null(fail-closed)", () => {
    expect(pickHideAtMs(slip("2027-04-01T12:00:00Z", null), 2)).toBeNull();
  });

  it("缺时间的那条恰好是最晚一场 → 仍然 null(不能拿剩下的腿取 max 糊弄)", () => {
    // 若实现改成"忽略缺时间的腿",这里会返回 T+2h 而不是 null,
    // 那意味着整单会在真正的最后一场还没开球时就提前撤下。
    expect(pickHideAtMs(slip("2027-04-01T12:00:00Z", null), 2)).toBeNull();
  });

  it("legs 为空 → null(不能让 Math.max() 变成 -Infinity)", () => {
    expect(pickHideAtMs(slip(), 2)).toBeNull();
  });

  it("date_only 的时间串 → null(§6.2.1 不得补零推断)", () => {
    expect(pickHideAtMs(slip("2027-04-01"), 2)).toBeNull();
  });

  it("无法解析的时间串 → null(NaN 不得穿透成时间)", () => {
    expect(pickHideAtMs(slip("not-a-time"), 2)).toBeNull();
  });

  it("无时区后缀的 ISO 按 UTC 解析,与带 Z 的结果完全相同", () => {
    // 回归:防止有人把 toExactEpochMs 换成裸 new Date(s)——那样无时区后缀
    // 会按浏览器本地时区解析,产生 8 小时误差。这条断言只有在非 UTC 机器上
    // 才会真正报警(CI 若是 UTC 则恒绿,但留着仍有意义)。
    expect(pickHideAtMs(slip("2027-04-01T12:00:00"), 2)).toBe(
      pickHideAtMs(slip("2027-04-01T12:00:00Z"), 2),
    );
  });

  it("hideAfterHours 确实参数化生效,不是硬编码 2", () => {
    expect(pickHideAtMs(slip("2027-04-01T12:00:00Z"), 0)).toBe(T);
    expect(pickHideAtMs(slip("2027-04-01T12:00:00Z"), 3)).toBe(T + 3 * HOUR);
  });
});

describe("visiblePublicPicks", () => {
  const s = slip("2027-04-01T12:00:00Z");
  const hideAt = T + 2 * HOUR;

  it("撤下时刻前一毫秒:仍展示", () => {
    expect(visiblePublicPicks([s], hideAt - 1, 2)).toEqual([s]);
  });

  it("恰好到点(开球满 2 小时):撤下(半开区间)", () => {
    expect(visiblePublicPicks([s], hideAt, 2)).toEqual([]);
  });

  it("过点之后:撤下", () => {
    expect(visiblePublicPicks([s], hideAt + 1, 2)).toEqual([]);
  });

  it("尚未开球的公推正常展示", () => {
    expect(visiblePublicPicks([s], T - 5 * HOUR, 2)).toEqual([s]);
  });

  it("混合场景只留下未到点的,且保持输入顺序", () => {
    const future = slip("2027-04-01T20:00:00Z");
    const justStarted = slip("2027-04-01T11:00:00Z"); // 开球 1 小时
    const longOver = slip("2027-04-01T08:00:00Z"); // 开球 4 小时
    const out = visiblePublicPicks([longOver, future, justStarted], T, 2);
    expect(out).toEqual([future, justStarted]);
  });

  it("缺开球时间的单不展示", () => {
    expect(visiblePublicPicks([slip(null)], T, 2)).toEqual([]);
  });

  it("全部过期 → 空数组(空态判据)", () => {
    expect(visiblePublicPicks([slip("2027-04-01T08:00:00Z")], T, 2)).toEqual([]);
  });

  it("空输入 → 空数组", () => {
    expect(visiblePublicPicks([], T, 2)).toEqual([]);
  });

  it("返回的是输入对象的同一引用,不是副本", () => {
    // §11.3 纪律:断言字段相等证明不了引用相等,这里显式用 toBe。
    const out = visiblePublicPicks([s], T, 2);
    expect(out[0]).toBe(s);
  });

  it("不原地修改输入数组", () => {
    const input = [slip("2027-04-01T08:00:00Z"), s];
    const snapshot = [...input];
    visiblePublicPicks(input, T, 2);
    expect(input).toEqual(snapshot);
  });
});

describe("comboLabel", () => {
  it("1 腿是单关,多腿是 N串1", () => {
    expect(comboLabel(1)).toBe("单关");
    expect(comboLabel(2)).toBe("2串1");
    expect(comboLabel(3)).toBe("3串1");
  });
});
