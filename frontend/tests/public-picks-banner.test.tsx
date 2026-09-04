/**
 * 首页「每日公推」banner 客户端渲染层(PublicPicksBannerLive)。
 *
 * 重点覆盖三件事:
 * 1. 水合安全——SSR 产物必须原样包含服务端传来的列表(nowMs===null 分支);
 * 2. 到点自动撤下——页面开着不动,定时器到点也要把过期的单摘掉;
 * 3. **不出现赔率**(本需求的硬性产品要求)。
 *
 * 组件首次取 now 走微任务(effect 体内不同步 setState,
 * react-hooks/set-state-in-effect),所以测试要用 `await flush()` 把微任务
 * 队列抽干,不能只 advanceTimersByTime——定时器推进不会 flush 微任务。
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PublicPicksBannerLive,
  type PublicPickSlip,
} from "@/components/home/PublicPicksBannerLive";

const HOUR = 3600_000;
const KICKOFF = "2027-04-01T12:00:00Z";
const KICKOFF_MS = Date.parse(KICKOFF);

/** 抽干微任务队列,让 effect 里那次 setNowMs 生效。 */
async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function slip(overrides: Partial<PublicPickSlip> = {}): PublicPickSlip {
  return {
    id: "slip-1",
    slip_date: "2027-04-01",
    title: "今日单关",
    combo_type: "single",
    published_at: "2027-04-01T08:00:00Z",
    legs: [
      {
        id: "leg-1",
        match_id: 9001,
        match_desc: "阿森纳 vs 切尔西 04-01 20:00",
        market: "ou",
        selection: "大2.5",
        kickoff_at_utc: KICKOFF,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("PublicPicksBannerLive", () => {
  it("服务端渲染输出原样包含传入的列表(水合安全)", () => {
    // 水合不匹配的根因是"初始 state 依赖 Date.now()"——SSR 的 now 与浏览器的
    // now 不同,首帧 HTML 与客户端首次 render 就对不上。这里用 renderToString
    // 直接检查 SSR 产物:即便系统时间已经远超撤下时刻,SSR 也必须原样输出
    // 服务端传进来的列表(nowMs===null 分支),不能在初始 render 里就过滤。
    //
    // 注意不能用 RTL 的 render() 测这一帧:它会同步 flush effect,
    // 拿到的已经是 effect 之后的状态,首帧无从观察。
    vi.setSystemTime(KICKOFF_MS + 10 * HOUR);
    const html = renderToString(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    expect(html).toContain("阿森纳 vs 切尔西 04-01 20:00");
  });

  it("挂载后,已过点的单被撤下", async () => {
    vi.setSystemTime(KICKOFF_MS + 3 * HOUR);
    const { container } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    expect(container.textContent).toBe("");
  });

  it("未到点的单正常展示比赛与「玩法 · 选项」", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    render(<PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />);
    await flush();
    expect(screen.getByText("阿森纳 vs 切尔西 04-01 20:00")).not.toBeNull();
    expect(screen.getByText("大小球 · 大2.5")).not.toBeNull();
    expect(screen.getByText("今日单关")).not.toBeNull();
  });

  it("页面开着不动,到点后自动撤下(定时器生效)", async () => {
    vi.setSystemTime(KICKOFF_MS + 2 * HOUR - 30_000); // 距撤下还有 30 秒
    const { container } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    expect(container.textContent).toContain("阿森纳");

    await act(async () => {
      vi.advanceTimersByTime(60_000); // 定时器再跑一轮,此时已过点
    });
    expect(container.textContent).toBe("");
  });

  it("**不出现任何赔率**", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    const { container } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    // fixture 里根本没有 odds 字段(DTO 就不含);这里再从渲染结果侧兜一道,
    // 防止将来有人从别处把赔率接回来。
    expect(container.textContent).not.toContain("@");
    expect(container.textContent).not.toMatch(/\d\.\d{2}/);
  });

  it("未知 market 原样显示,不崩(?? leg.market 兜底)", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    render(
      <PublicPicksBannerLive
        slips={[
          slip({
            legs: [
              {
                id: "leg-1",
                match_id: 9001,
                match_desc: "A vs B",
                market: "btts",
                selection: "双方进球",
                kickoff_at_utc: KICKOFF,
              },
            ],
          }),
        ]}
        hideAfterHours={2}
      />,
    );
    await flush();
    expect(screen.getByText("btts · 双方进球")).not.toBeNull();
  });

  it("串关渲染全部腿并标出 2串1", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    render(
      <PublicPicksBannerLive
        slips={[
          slip({
            combo_type: "parlay",
            legs: [
              {
                id: "leg-1", match_id: 9001, match_desc: "早场 A vs B",
                market: "1x2", selection: "主胜", kickoff_at_utc: KICKOFF,
              },
              {
                id: "leg-2", match_id: 9002, match_desc: "晚场 C vs D",
                market: "ah", selection: "让-0.5 主",
                kickoff_at_utc: "2027-04-01T14:00:00Z",
              },
            ],
          }),
        ]}
        hideAfterHours={2}
      />,
    );
    await flush();
    expect(screen.getByText("早场 A vs B")).not.toBeNull();
    expect(screen.getByText("晚场 C vs D")).not.toBeNull();
    expect(screen.getByText("2串1")).not.toBeNull();
  });

  it("串关按最后一场开球算:最早那场已过 2 小时仍然展示", async () => {
    // 早场开球 3 小时前(单看它已该撤),晚场还没开球 → 整单必须仍在。
    vi.setSystemTime(KICKOFF_MS + 3 * HOUR);
    const { container } = render(
      <PublicPicksBannerLive
        slips={[
          slip({
            combo_type: "parlay",
            legs: [
              {
                id: "leg-1", match_id: 9001, match_desc: "早场 A vs B",
                market: "1x2", selection: "主胜", kickoff_at_utc: KICKOFF,
              },
              {
                id: "leg-2", match_id: 9002, match_desc: "晚场 C vs D",
                market: "1x2", selection: "客胜",
                kickoff_at_utc: "2027-04-01T20:00:00Z",
              },
            ],
          }),
        ]}
        hideAfterHours={2}
      />,
    );
    await flush();
    expect(container.textContent).toContain("早场 A vs B");
  });

  it("全部过期 → 组件返回 null,容器内无任何节点", async () => {
    vi.setSystemTime(KICKOFF_MS + 5 * HOUR);
    const { container } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    expect(container.innerHTML).toBe("");
  });

  it("链接指向 /reco?tab=public", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    const { container } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    expect(container.querySelector("a")?.getAttribute("href")).toBe(
      "/reco?tab=public",
    );
  });

  it("卸载后定时器被清掉(不泄漏、不在卸载后 setState)", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    const { unmount } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
