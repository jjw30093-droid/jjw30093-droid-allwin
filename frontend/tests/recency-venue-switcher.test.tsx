/**
 * RecencyVenueSwitcher(2026-09-15 改下拉):两个 <select> 独立选择场次范围/
 * 主客场,onChange 时用 router.push() 跳到正确的 URL——URL 仍是唯一真源,
 * 只是触发方式从"点链接"变成"选完自动跳转"。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

import { RecencyVenueSwitcher } from "@/components/league/RecencyVenueSwitcher";

afterEach(() => {
  cleanup();
  push.mockClear();
});

describe("RecencyVenueSwitcher", () => {
  it("未筛选时两个下拉都显示「全部」选项", () => {
    render(<RecencyVenueSwitcher leagueId="47" />);
    const recencySelect = screen.getByLabelText("选择场次范围") as HTMLSelectElement;
    const venueSelect = screen.getByLabelText("选择主客场") as HTMLSelectElement;
    expect(recencySelect.value).toBe("");
    expect(venueSelect.value).toBe("all");
  });

  it("选「最近5场」跳转到带 recency=5 的链接,不带 venue(缺省)", () => {
    render(<RecencyVenueSwitcher leagueId="47" />);
    fireEvent.change(screen.getByLabelText("选择场次范围"), { target: { value: "5" } });
    expect(push).toHaveBeenCalledWith("/league/47/team-stats?recency=5");
  });

  it("选「主场」跳转到带 venue=home 的链接,不带 recency", () => {
    render(<RecencyVenueSwitcher leagueId="47" />);
    fireEvent.change(screen.getByLabelText("选择主客场"), { target: { value: "home" } });
    expect(push).toHaveBeenCalledWith("/league/47/team-stats?venue=home");
  });

  it("已选 recency 时再选 venue,两个维度的参数都保留,互不清空", () => {
    render(<RecencyVenueSwitcher leagueId="47" recency={10} />);
    fireEvent.change(screen.getByLabelText("选择主客场"), { target: { value: "away" } });
    expect(push).toHaveBeenCalledWith("/league/47/team-stats?recency=10&venue=away");
  });

  it("season 一并携带,切筛选维度时不掉赛季选择", () => {
    render(<RecencyVenueSwitcher leagueId="47" season="2024/2025" recency={5} />);
    fireEvent.change(screen.getByLabelText("选择主客场"), { target: { value: "home" } });
    expect(push).toHaveBeenCalledWith(
      "/league/47/team-stats?season=2024%2F2025&recency=5&venue=home",
    );
  });

  it("切回「全部场次」时链接不带 recency 参数", () => {
    render(<RecencyVenueSwitcher leagueId="47" recency={5} venue="home" />);
    fireEvent.change(screen.getByLabelText("选择场次范围"), { target: { value: "" } });
    expect(push).toHaveBeenCalledWith("/league/47/team-stats?venue=home");
  });
});
