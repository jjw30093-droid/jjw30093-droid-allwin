/**
 * Admin「精选授权」Tab 表单交互(2026-08-16,取代旧的全局 reco:daily/
 * daily_picks 布尔权益;2026-08-21 起"确认授权"从 window.confirm 改成站内
 * 二次确认面板——原生弹窗在部分浏览器/系统环境下会被忽略/划掉,真实 admin
 * 报告点击没反应)。
 *
 * 覆盖:
 * - 搜索并选择用户 + 推荐单后,点击"授权"展开站内确认面板,点击"确认授权"
 *   才真正提交,请求体是 {user_id, slip_id, note};
 * - 撤销走"点击撤销 → 展开原因输入 → 确认撤销"的二次确认交互(与页面里
 *   其它高风险操作——如推荐单作废——同一套交互模式,不是 window.confirm)。
 * - 2026-10 起默认是「按时段」开通(用户 + 起止日期,北京时间含首尾):一周 = 起始日 +6 天,
 *   一个月 = 起始日 +29 天;按单场要先切到「按单场」。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessTab } from "@/app/admin/page";

/** 与页面同口径:北京时间今天 */
function beijingToday(): string {
  return new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
}
function addDays(day: string, n: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);
}
function toSlipMode() {
  fireEvent.click(screen.getByRole("button", { name: "按单场" }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };

function mockFetch(opts: {
  grants?: unknown[];
  users?: unknown[];
  slips?: unknown[];
  periods?: unknown[];
} = {}): Call[] {
  const calls: Call[] = [];
  const grants = opts.grants ?? [];
  const users = opts.users ?? [];
  const slips = opts.slips ?? [];
  const periods = opts.periods ?? [];

  const impl = vi.fn((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });

    const json = (payload: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );

    if (method === "GET" && url.includes("/api/v1/admin/users")) {
      return json({ total: users.length, users });
    }
    if (method === "GET" && url.includes("/api/v1/admin/reco/slips")) {
      return json({ total: slips.length, slips });
    }
    if (method === "GET" && url.includes("/api/v1/admin/reco/access-grants")) {
      return json({ total: grants.length, grants });
    }
    if (method === "GET" && url.includes("/api/v1/admin/reco/access-periods")) {
      return json({ total: periods.length, periods });
    }
    if (method === "POST" && /\/access-grants\/[^/]+\/revoke$/.test(url)) {
      return json({ status: "ok" });
    }
    if (method === "POST" && url.endsWith("/access-grants")) {
      return json({
        id: "grant-new-1",
        user_id: body?.user_id,
        slip_id: body?.slip_id,
        slip_title: "被选中的推荐单",
        slip_date: "2026-08-20",
        status: "active",
        granted_at: "2026-08-16T08:00:00Z",
        granted_by: "admin-1",
        revoked_at: null,
        revoked_by: null,
        note: body?.note ?? null,
        created_at: "2026-08-16T08:00:00Z",
        updated_at: "2026-08-16T08:00:00Z",
      });
    }
    return json({ status: "ok" });
  });
  vi.stubGlobal("fetch", impl);
  return calls;
}

describe("精选授权:提交新授权", () => {
  it("搜索选择用户和推荐单后提交,POST 请求体正确,展示成功提示", async () => {
    const calls = mockFetch({
      grants: [],
      users: [
        { id: "user-1", display_name: "小明", role: "user", status: "active",
          created_at: "2026-08-01T00:00:00Z", last_login_at: null, plan_id: "member", plan_ends_at: null },
      ],
      slips: [
        { id: "slip-1", slip_date: "2026-08-20", title: "被选中的推荐单", note: null,
          combo_type: "single", status: "published", result: null, return_units: null,
          published_at: "2026-08-19T08:00:00Z", settled_at: null, edit_count: 0,
          last_edited_at: "2026-08-19T08:00:00Z", legs: [] },
      ],
    });

    render(<AccessTab />);
    toSlipMode();

    const userInput = screen.getByPlaceholderText("搜索用户(昵称 / 用户编号)");
    fireEvent.focus(userInput);
    fireEvent.change(userInput, { target: { value: "小明" } });
    const userOption = await screen.findByRole("button", { name: /小明/ });
    fireEvent.click(userOption);

    const slipInput = screen.getByPlaceholderText("搜索推荐单(标题 / 日期 / ID)");
    fireEvent.focus(slipInput);
    fireEvent.change(slipInput, { target: { value: "被选中" } });
    const slipOption = await screen.findByRole("button", { name: /被选中的推荐单/ });
    fireEvent.click(slipOption);

    await waitFor(() =>
      expect((screen.getByRole("button", { name: "授权" }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    // 第一次点击只展开站内确认面板,不应该立即发出请求。
    fireEvent.click(screen.getByRole("button", { name: "授权" }));
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/access-grants"))).toBe(false);

    // "确认授权"只在展开确认面板后才出现,证明真的是两步确认。
    const confirmBtn = await screen.findByRole("button", { name: "确认授权" });
    fireEvent.click(confirmBtn);

    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "POST" && c.url.endsWith("/access-grants")),
      ).toBe(true),
    );
    const postCall = calls.find((c) => c.method === "POST" && c.url.endsWith("/access-grants"))!;
    expect(postCall.body).toEqual({ user_id: "user-1", slip_id: "slip-1", note: null });

    await waitFor(() => expect(screen.queryByText(/已授权/)).not.toBeNull());
  });
});

describe("精选授权:撤销走二次确认交互(不是 window.confirm)", () => {
  it("点击撤销展开原因输入,点击确认撤销才真正发出 POST 请求", async () => {
    const calls = mockFetch({
      grants: [
        {
          id: "grant-1", user_id: "user-1", slip_id: "slip-1",
          slip_title: "待撤销推荐单", slip_date: "2026-08-15",
          status: "active", granted_at: "2026-08-15T08:00:00Z", granted_by: "admin-1",
          revoked_at: null, revoked_by: null, note: null,
          created_at: "2026-08-15T08:00:00Z", updated_at: "2026-08-15T08:00:00Z",
        },
      ],
    });

    render(<AccessTab />);
    toSlipMode();

    await waitFor(() => expect(screen.queryByText("待撤销推荐单")).not.toBeNull());

    // 点击"撤销"只展开表单,不应该立即发出撤销请求。
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/revoke"))).toBe(false);

    const reasonInput = screen.getByPlaceholderText("撤销原因(可空)");
    fireEvent.change(reasonInput, { target: { value: "测试撤销原因" } });
    fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.includes("/revoke"))).toBe(true),
    );
    const revokeCall = calls.find((c) => c.method === "POST" && c.url.includes("/revoke"))!;
    expect(revokeCall.url).toContain("grant-1/revoke");
    expect(revokeCall.body).toEqual({ reason: "测试撤销原因" });

    // "已撤销"同时出现在筛选下拉的 option 里,用 getAllByText 避免歧义。
    await waitFor(() => expect(screen.getAllByText("已撤销").length).toBeGreaterThan(0));
  });
});

describe("精选授权:按时段开通(默认)", () => {
  const USER = {
    id: "user-9", display_name: "老王", short_code: "A1B2C3", role: "user", status: "active",
    created_at: "2026-09-01T00:00:00Z", last_login_at: null, plan_id: "free", plan_ends_at: null,
  };

  it("选用户 + 一个月,确认后 POST {user_id, starts_on, ends_on(起始 +29 天), note}", async () => {
    const calls = mockFetch({ users: [USER] });
    render(<AccessTab />);

    const userInput = screen.getByPlaceholderText("搜索用户(昵称 / 用户编号)");
    fireEvent.focus(userInput);
    fireEvent.change(userInput, { target: { value: "a1b2" } });
    const opt = await screen.findByRole("button", { name: /老王 · 编号 A1B2C3/ });
    fireEvent.click(opt);

    fireEvent.click(screen.getByRole("button", { name: "一个月" }));
    const today = beijingToday();
    expect(screen.getByText(`至 ${addDays(today, 29)}(含)`)).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "开通" }));
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/access-periods"))).toBe(false);
    fireEvent.click(await screen.findByRole("button", { name: "确认开通" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/access-periods"))).toBe(true),
    );
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/access-periods"))!;
    expect(post.body).toEqual({ user_id: "user-9", starts_on: today, ends_on: addDays(today, 29), note: null });
    await waitFor(() => expect(screen.queryByText(/已开通/)).not.toBeNull());
  });

  it("默认一周 = 起始日 +6 天;自定义结束早于开始时不能开通", async () => {
    mockFetch({ users: [USER] });
    render(<AccessTab />);
    const today = beijingToday();
    expect(screen.getByText(`至 ${addDays(today, 6)}(含)`)).not.toBeNull();

    const userInput = screen.getByPlaceholderText("搜索用户(昵称 / 用户编号)");
    fireEvent.focus(userInput);
    fireEvent.change(userInput, { target: { value: "老王" } });
    fireEvent.click(await screen.findByRole("button", { name: /老王/ }));

    fireEvent.click(screen.getByRole("button", { name: "自定义" }));
    const end = screen.getByLabelText("结束") as HTMLInputElement;
    fireEvent.change(end, { target: { value: addDays(today, -1) } });
    expect((screen.getByRole("button", { name: "开通" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("结束日期不能早于开始日期")).not.toBeNull();
  });

  it("列表显示昵称与编号;撤销走二次确认", async () => {
    const calls = mockFetch({
      periods: [
        {
          id: "period-1", user_id: "user-9", starts_on: "2026-09-01", ends_on: "2026-09-30",
          status: "active", granted_at: "2026-09-01T02:00:00Z", granted_by: "admin-1",
          revoked_at: null, revoked_by: null, note: "月卡",
          created_at: "2026-09-01T02:00:00Z", updated_at: "2026-09-01T02:00:00Z",
          user_display_name: "老王", user_short_code: "A1B2C3",
        },
      ],
    });
    render(<AccessTab />);
    await waitFor(() => expect(screen.queryByText("A1B2C3")).not.toBeNull());
    expect(screen.getByText("老王")).not.toBeNull();
    expect(screen.getByText("月卡")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/revoke"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "确认撤销(期内全部收回)" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.includes("access-periods/period-1/revoke"))).toBe(true),
    );
  });
});
