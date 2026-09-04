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

/** 带结构化比赛事实的腿(联赛 + 主客队)。crest_url 为 null 是常见合法状态
 *  ——媒体管线还没采到这支球队的队徽,TeamBadge 走两字缩写兜底。 */
const STRUCTURED_LEG: PublicPickSlip["legs"][number] = {
  id: "leg-1",
  match_id: 9001,
  match_desc: "阿森纳 vs 切尔西 04-01 20:00",
  market: "ou",
  selection: "大2.5",
  kickoff_at_utc: KICKOFF,
  league_id: 47,
  league_name_zh: "英超",
  home: { team_id: 1001, name: "阿森纳", crest_url: null },
  away: { team_id: 1002, name: "切尔西", crest_url: null },
};

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

  it("未到点的单正常展示比赛、时间、玩法与选项", async () => {
    // 2026-09-04 生产真机实测后腿行改成两行:第一行队徽+完整队名,第二行
    // 时间/玩法(次级)+ 选项(主角)。单行版本在真实数据下把「皇家贝蒂斯」
    // 压到了 7px,玩法名当时被迫退进 title;两行腾出空间后玩法回到可见文案。
    vi.setSystemTime(KICKOFF_MS - HOUR);
    render(<PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />);
    await flush();
    expect(screen.getByText("阿森纳 vs 切尔西 04-01 20:00")).not.toBeNull();
    expect(screen.getByText("大2.5")).not.toBeNull();
    expect(screen.getByText("大小球")).not.toBeNull();
    // 横条不渲染 slip 标题,标签列给的是 comboLabel(单关 / N串1)。
    expect(screen.getByText("单关")).not.toBeNull();
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
    // `?? leg.market` 兜底:未登记的 market 原样渲染,不因查不到映射而崩。
    expect(screen.getByText("双方进球")).not.toBeNull();
    expect(screen.getByText("btts")).not.toBeNull();
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

  it("有结构化比赛事实时画联赛徽与两枚队徽,并渲染中文队名", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    const { container } = render(
      <PublicPicksBannerLive slips={[slip({ legs: [STRUCTURED_LEG] })]} hideAfterHours={2} />,
    );
    await flush();
    expect(screen.getByText("阿森纳")).not.toBeNull();
    expect(screen.getByText("切尔西")).not.toBeNull();
    // 联赛徽走自托管静态图(/brand/leagues/{id}.png),不是外链。
    const league = container.querySelector('img[src*="/brand/leagues/47"]');
    expect(league).not.toBeNull();
    // crest_url 为 null → TeamBadge 走两字缩写兜底,不是错误态。
    expect(container.querySelectorAll('[data-testid="team-badge-fallback"]').length).toBe(2);
    // 有了结构化字段就不再重复渲染录入时的 match_desc 文本。
    expect(container.textContent).not.toContain("阿森纳 vs 切尔西 04-01 20:00");
  });

  it("缺 home/away 时退回 match_desc 文本——少画图标,不藏腿", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    const { container } = render(
      <PublicPicksBannerLive slips={[slip()]} hideAfterHours={2} />,
    );
    await flush();
    expect(screen.getByText("阿森纳 vs 切尔西 04-01 20:00")).not.toBeNull();
    expect(container.querySelector('[data-testid="team-badge-fallback"]')).toBeNull();
    expect(container.querySelector('[data-testid="team-badge-image"]')).toBeNull();
  });

  it("开球与 slip_date 同一北京日 → 只出钟点", async () => {
    vi.setSystemTime(KICKOFF_MS - HOUR);
    // KICKOFF = 2027-04-01T12:00:00Z → 北京 20:00,与 slip_date 2027-04-01 同日。
    render(
      <PublicPicksBannerLive slips={[slip({ legs: [STRUCTURED_LEG] })]} hideAfterHours={2} />,
    );
    await flush();
    expect(screen.getByText("20:00")).not.toBeNull();
    expect(screen.queryByText(/月.*日/)).toBeNull();
  });

  it("开球跨到 slip_date 的次日 → 带上日期,不让读者以为是当天", async () => {
    // 2027-04-01T17:00:00Z → 北京 4月2日 01:00,与 slip_date 2027-04-01 不同日。
    const late = "2027-04-01T17:00:00Z";
    vi.setSystemTime(Date.parse(late) - HOUR);
    render(
      <PublicPicksBannerLive
        slips={[slip({ legs: [{ ...STRUCTURED_LEG, kickoff_at_utc: late }] })]}
        hideAfterHours={2}
      />,
    );
    await flush();
    expect(screen.getByText("4月2日 01:00")).not.toBeNull();
  });

  it("长队名 + 长选项 + 跨日时间同时出现时,队名与选项都完整渲染", async () => {
    // 2026-09-04 生产真实数据回归:单行结构下「皇家贝蒂斯」被压到 7px
    // (固定开销 171px,只剩 16px 给两个队名)。两行结构后队名独占第一行,
    // 与时间/玩法/选项不再抢宽度。
    vi.setSystemTime(Date.parse("2027-04-01T10:00:00Z"));
    render(
      <PublicPicksBannerLive
        slips={[slip({
          slip_date: "2027-04-01",
          legs: [{
            id: "leg-long",
            match_id: 9001,
            match_desc: "皇家贝蒂斯 vs 皇家马德里 04-02 03:00",
            market: "ah",
            selection: "客队让1.25球",
            kickoff_at_utc: "2027-04-01T19:00:00Z",   // 北京 4月2日 03:00,跨日
            league_id: 87,
            league_name_zh: "西甲",
            home: { team_id: 1, name: "皇家贝蒂斯", crest_url: null },
            away: { team_id: 2, name: "皇家马德里", crest_url: null },
          }],
        })]}
        hideAfterHours={2}
      />,
    );
    await flush();
    expect(screen.getByText("皇家贝蒂斯")).not.toBeNull();
    expect(screen.getByText("皇家马德里")).not.toBeNull();
    expect(screen.getByText("客队让1.25球")).not.toBeNull();
    // 跨日 → 带上日期,不让读者以为是当天
    expect(screen.getByText("4月2日 03:00")).not.toBeNull();
    expect(screen.getByText("亚洲让球")).not.toBeNull();
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
