/**
 * /pricing 页面测试(2026-08-16 权限口径修正)。
 *
 * 后端已确认:除"每日精选"外,网站所有比赛内容全部免费,包括对匿名用户
 * ——登录和内容分层彻底解耦。此前页面的三层权限说明表把"完整胜平负三项
 * 概率""赔率时间轴与变化记录"等描述成 member 档独占权益,暗示匿名用户
 * 看不到这些内容——这个框架性描述现在是错误的,必须删除或改写。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessTiers } from "@/app/pricing/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PLANS_BODY = {
  plans: [
    { id: "free", name_zh: "游客", rank: 0, description: "" },
    { id: "member", name_zh: "注册用户", rank: 0, description: "" },
    { id: "daily_picks", name_zh: "精选授权用户", rank: 1, description: "" },
  ],
  products: [],
};

function mockProductsFetch() {
  const headers = new Headers();
  headers.set("content-type", "application/json");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(PLANS_BODY), { status: 200, headers })),
  );
}

describe("/pricing:不得再把普通比赛内容描述成登录/会员独占权益", () => {
  it("游客(匿名)卡片不得声称只能看到部分数据/最高一项概率/延迟赔率概要", async () => {
    mockProductsFetch();
    render(await AccessTiers());

    expect(screen.getByText("注册用户")).not.toBeNull();
    expect(screen.queryByText(/浏览部分公开联赛/)).toBeNull();
    expect(screen.queryByText(/每场比赛的最高一项模型概率/)).toBeNull();
    expect(screen.queryByText(/延迟赔率概要/)).toBeNull();
  });

  it("注册用户卡片不得声称'完整胜平负三项概率''赔率时间轴'是登录后才解锁的足球数据", async () => {
    mockProductsFetch();
    render(await AccessTiers());

    expect(screen.queryByText(/完整胜平负三项概率与比分矩阵/)).toBeNull();
    expect(screen.queryByText(/赔率时间轴与变化记录/)).toBeNull();
    expect(screen.queryByText(/全部联赛的完整足球数据/)).toBeNull();
  });
});
