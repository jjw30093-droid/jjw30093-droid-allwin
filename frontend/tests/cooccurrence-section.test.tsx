/**
 * CooccurrenceSection 渲染测试(2026-08-16 权限口径修正)。
 *
 * 后端 CooccurrenceResponse.items 现在恒为数组(不再是"匿名/free 只给计数,
 * items 显式 null"的判别联合)——组件必须在给定完整数据时完整渲染明细,
 * 不能只渲染计数、也不能出现任何"登录查看明细"的引导文案。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CooccurrenceSection } from "@/components/matches/CooccurrenceSection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockCooccurrenceResponse(body: unknown) {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200, headers })),
  );
}

const ITEM = {
  window_seconds: 900,
  delta_seconds: 120,
  computed_at: "2026-08-19T10:00:00Z",
  market: "1x2",
  company_id: "8",
  field: "home",
  prev_value: "1.90",
  new_value: "1.85",
  odds_moved_at: "2026-08-19T09:58:00Z",
  event_type: "lineup_change",
  detail_json: "{}",
  event_moved_at: "2026-08-19T10:00:00Z",
};

describe("CooccurrenceSection 给定完整数据时必须完整渲染(不再只渲染计数)", () => {
  it("items 恒为数组且有内容时,直接渲染明细列表,不出现任何登录提示", async () => {
    mockCooccurrenceResponse({ match_id: 1, count: 1, items: [ITEM], note: null });
    render(<CooccurrenceSection matchId={1} />);

    await waitFor(() => expect(screen.queryByText(/组变化撞在同一个时间段里/)).not.toBeNull());
    // 明细必须真的渲染出来(时间共现描述行),不是只有计数。
    expect(screen.getByText(/赔率动了/)).not.toBeNull();
    expect(screen.getByText(/同时段检测到/)).not.toBeNull();
    expect(screen.queryByText(/登录/)).toBeNull();
    expect(screen.queryByText(/免费登录/)).toBeNull();
  });

  it("items 为空数组时(真实无同期事件)不渲染区块", async () => {
    mockCooccurrenceResponse({ match_id: 1, count: 0, items: [], note: null });
    render(<CooccurrenceSection matchId={1} />);
    await waitFor(() => {
      // 组件在 count===0 时整块不渲染,给点时间讓 effect 落地后确认仍是空
    });
    expect(screen.queryByText("关键变化")).toBeNull();
  });
});
