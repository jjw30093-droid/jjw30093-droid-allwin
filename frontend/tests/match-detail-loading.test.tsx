/**
 * app/matches/[matchId]/loading.tsx 渲染测试。
 *
 * 背景:该动态路由此前没有 loading.tsx。Next.js 16 的 <Link> 在
 * touchstart 时就会发起该路由的预取(segment cache),点击态触发的正式
 * 导航要等这个预取结果——路由没有 loading.tsx 兜底时,用户在这段等待期
 * 只会看到"点了没反应"的空白比赛列表页,和真实生产反馈的"点击比赛跳转
 * 不到详情页"症状一致(node_modules/next/dist/docs 对此有专门警告)。
 * 桌面鼠标点击/程序化 .click() 不触发 touchstart,不会经过这条预取路径,
 * 这也是同一个 bug 只在触屏上出现的原因。
 *
 * loading.tsx 补的是诚实的"加载中"骨架态,不是掩盖延迟——沿用
 * MemberMatchDetail 已经在用的同一套骨架样式(MemberLeagueSection.module.css
 * 的 .skeleton/.skelLine),保证视觉与既有加载态一致。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import Loading from "@/app/matches/[matchId]/loading";

afterEach(() => {
  cleanup();
});

describe("app/matches/[matchId]/loading", () => {
  it("渲染带 aria-label 的加载骨架,不是空白页", () => {
    render(<Loading />);
    expect(screen.getByLabelText("比赛详情加载中")).toBeTruthy();
  });

  it("骨架至少有 3 条占位线(呼应头部/概率卡/证据三段)", () => {
    const { container } = render(<Loading />);
    const lines = container.querySelectorAll("[class*=skelLine]");
    expect(lines.length).toBeGreaterThanOrEqual(3);
  });
});
