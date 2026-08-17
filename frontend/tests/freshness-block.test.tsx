/**
 * 首页「今日更新状态」条(HomeMatchExperienceLive.tsx::FreshnessBlock)。
 *
 * 背景(2026-08-16 首页粗糙度修复):这三条时间戳此前只用 formatBeijingHM()
 * 显示 HH:mm,用户完全判断不出这是今天还是好几天前的数据,也看不出某个
 * 数据源当前是否已经失败。
 *
 * 关键断言:
 * - 3 天前的时间戳必须渲染出完整日期(不能只有裸 HH:mm);
 * - "今天"的时间戳允许显示"今天 HH:mm"这种简写;
 * - STALE 状态必须有可见的文字指示(复用 lib/product-status.ts 的
 *   syncStateLabel(),不是第二套翻译)。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FreshnessBlock } from "@/components/home/HomeMatchExperienceLive";
import type { GetJson } from "@/lib/api-v1";

type Freshness = GetJson<"/api/v1/status/freshness">;

afterEach(() => {
  cleanup();
});

function isoHoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

function baseFreshness(overrides: Partial<Freshness> = {}): Freshness {
  return {
    schedule_updated_at: null,
    odds_updated_at: null,
    reco_updated_at: null,
    schedule_state: "UNAVAILABLE",
    odds_state: "UNAVAILABLE",
    reco_state: "UNAVAILABLE",
    ...overrides,
  } as Freshness;
}

describe("FreshnessBlock 时间展示(bug:裸 HH:mm 判断不出是不是今天)", () => {
  it("3 天前的时间戳必须显示完整日期,不能只有 HH:mm", () => {
    const threeDaysAgo = isoHoursAgo(72);
    render(
      <FreshnessBlock
        freshness={baseFreshness({
          schedule_updated_at: threeDaysAgo,
          schedule_state: "STALE",
        })}
      />,
    );
    const line = screen.getByTestId("freshness-line");
    // 完整北京日期形如 "YYYY-MM-DD"(formatBeijingDateTime)或中文短格式
    // "M月D日"(formatBeijingZh)——不管具体格式,必须包含年月日信息,
    // 不能是裸的 "HH:mm"。
    expect(line.textContent).toMatch(/\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日/);
    // 3 天前不是"今天",不能出现"今天"简写字样贴着这个时间戳。
    expect(line.textContent).not.toMatch(/今天\s*\d{2}:\d{2}/);
  });

  it('"今天"的时间戳允许显示"今天 HH:mm"简写', async () => {
    const justNow = isoHoursAgo(0.05); // 3 分钟前,必然还是北京时间的今天
    render(
      <FreshnessBlock
        freshness={baseFreshness({
          odds_updated_at: justNow,
          odds_state: "FRESH",
        })}
      />,
    );
    const line = screen.getByTestId("freshness-line");
    // "今天"判定要等客户端挂载后的 effect(setTimeout(0),避免 effect 内联
    // setState 触发级联渲染,与 KickoffCountdown.tsx 同一套写法)算出北京
    // 日期才会生效——挂载瞬间(含 SSR)先按完整日期回退,不冒险显示错误的
    // "今天"。
    await waitFor(() => {
      expect(line.textContent).toMatch(/今天\s*\d{2}:\d{2}/);
    });
  });

  it("没有任何时间戳时三条都显示尚无记录,不伪造时间", () => {
    render(<FreshnessBlock freshness={baseFreshness()} />);
    const line = screen.getByTestId("freshness-line");
    expect(line.textContent).toContain("尚无记录");
  });
});

describe("FreshnessBlock 状态指示(bug:看不出数据源是否失败)", () => {
  it("STALE 状态必须有可见的文字指示,复用 syncStateLabel 既有翻译", () => {
    render(
      <FreshnessBlock
        freshness={baseFreshness({
          schedule_updated_at: isoHoursAgo(30),
          schedule_state: "STALE",
        })}
      />,
    );
    expect(screen.getByText("数据等待刷新")).not.toBeNull();
  });

  it("UNAVAILABLE 状态必须有可见的文字指示", () => {
    render(
      <FreshnessBlock
        freshness={baseFreshness({
          reco_updated_at: null,
          reco_state: "UNAVAILABLE",
        })}
      />,
    );
    // baseFreshness 默认三条都是 UNAVAILABLE(没有种子数据时的真实响应形状),
    // 这里只需确认这个状态确实渲染出可见文字,不要求唯一匹配。
    expect(screen.getAllByText("部分数据暂不可用").length).toBeGreaterThan(0);
  });

  it("FRESH 状态同样有可见的文字指示", () => {
    render(
      <FreshnessBlock
        freshness={baseFreshness({
          odds_updated_at: isoHoursAgo(0.1),
          odds_state: "FRESH",
        })}
      />,
    );
    expect(screen.getByText("数据已更新")).not.toBeNull();
  });
});
