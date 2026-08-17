/**
 * Admin「精选授权」Tab 表单交互(2026-08-16,取代旧的全局 reco:daily/
 * daily_picks 布尔权益)。
 *
 * 覆盖:
 * - 搜索并选择用户 + 推荐单后提交授权,请求体是 {user_id, slip_id, note};
 * - 撤销走"点击撤销 → 展开原因输入 → 确认撤销"的二次确认交互(与页面里
 *   其它高风险操作——如推荐单作废——同一套交互模式,不是 window.confirm)。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessTab } from "@/app/admin/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };

function mockFetch(opts: {
  grants?: unknown[];
  users?: unknown[];
  slips?: unknown[];
} = {}): Call[] {
  const calls: Call[] = [];
  const grants = opts.grants ?? [];
  const users = opts.users ?? [];
  const slips = opts.slips ?? [];

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

    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AccessTab />);

    const userInput = screen.getByPlaceholderText("搜索用户(昵称 / 用户 ID)");
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
      expect((screen.getByRole("button", { name: "确认授权" }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认授权" }));

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
