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

    // 2026-09-26:当前没有自助注册,"注册用户"改称"免费账号",开通方式写成"通过公众号申请"
    expect(screen.getByText("免费账号")).not.toBeNull();
    expect(screen.queryByText("注册用户")).toBeNull();
    expect(screen.getByText("通过公众号申请开通，无需付费")).not.toBeNull();
    expect(screen.getByText("按场为你的账号开通")).not.toBeNull();
    expect(screen.queryByText(/浏览部分公开联赛/)).toBeNull();
    expect(screen.queryByText(/每场比赛的最高一项模型概率/)).toBeNull();
    expect(screen.queryByText(/延迟赔率概要/)).toBeNull();
  });

  it("免费账号卡片不得声称'完整胜平负三项概率''赔率时间轴'是登录后才解锁的足球数据", async () => {
    mockProductsFetch();
    render(await AccessTiers());

    expect(screen.queryByText(/完整胜平负三项概率与比分矩阵/)).toBeNull();
    expect(screen.queryByText(/赔率时间轴与变化记录/)).toBeNull();
    expect(screen.queryByText(/全部联赛的完整足球数据/)).toBeNull();
  });
});

describe("/pricing 页面文案与入口(2026-09-26)", () => {
  it("标题是'会员与权限',三步开通指引在最上面(标题之后、套餐卡之前)", async () => {
    const { default: PricingPage } = await import("@/app/pricing/page");
    const { container } = render(<PricingPage />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("会员与权限");
    const guide = container.querySelector("#how-to-unlock")!;
    expect(guide).not.toBeNull();
    const steps = [...guide.querySelectorAll("ol > li")].map((li) => li.textContent);
    expect(steps).toEqual(["登录账号", "通过公众号联系我们开通", "回到「精选」页查看"]);
    // 三步指引在概述段落之前
    const subtitle = container.querySelector("p")!;
    expect(guide.compareDocumentPosition(subtitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("页面上没有'站长'、没有'看历史 战绩'式的多余空格", async () => {
    const { default: PricingPage } = await import("@/app/pricing/page");
    const { container } = render(<PricingPage />);
    expect(container.textContent).not.toContain("站长");
    expect(container.textContent).toContain("看历史战绩");
    expect(container.textContent).not.toContain("历史 战绩");
  });
});
