/**
 * SeasonSwitcher(2026-09-15 改下拉):<select> 切赛季时 tableType/recency/
 * venue 必须互相带着走,"自动"选项的标签随 resolved 变化,单赛季/零赛季/
 * 赛季不在列表里(selectedUnknown)三种边界各自的降级行为。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

import { SeasonSwitcher } from "@/components/league/SeasonSwitcher";

afterEach(() => {
  cleanup();
  push.mockClear();
});

const SEASONS = ["2023/2024", "2024/2025", "2025/2026"];

describe("SeasonSwitcher", () => {
  it("零赛季时不渲染任何东西", () => {
    const { container } = render(
      <SeasonSwitcher leagueId="47" section="team-stats" seasons={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("只有一个赛季时渲染静态徽章,不是下拉", () => {
    render(<SeasonSwitcher leagueId="47" section="team-stats" seasons={["2025/2026"]} />);
    expect(screen.getByText("2025/2026")).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("倒序渲染赛季选项(最新在前),默认态的选项直接显示 resolved,不写「自动」二字", () => {
    render(<SeasonSwitcher leagueId="47" section="team-stats" seasons={SEASONS} resolved="2025/2026" />);
    const select = screen.getByLabelText("选择赛季") as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map((o) => o.textContent);
    expect(optionLabels).toEqual(["2025/2026", "2025/2026", "2024/2025", "2023/2024"]);
    expect(select.value).toBe("");
  });

  it("resolved 未知时默认态的选项退化到最新赛季,同样不写「自动」二字", () => {
    render(<SeasonSwitcher leagueId="47" section="team-stats" seasons={SEASONS} />);
    const select = screen.getByLabelText("选择赛季") as HTMLSelectElement;
    expect(select.options[0].textContent).toBe("2025/2026");
  });

  it("选一个具体赛季时下拉的当前值是该赛季,不是自动", () => {
    render(
      <SeasonSwitcher
        leagueId="47"
        section="team-stats"
        seasons={SEASONS}
        selected="2024/2025"
        resolved="2024/2025"
      />,
    );
    const select = screen.getByLabelText("选择赛季") as HTMLSelectElement;
    expect(select.value).toBe("2024/2025");
  });

  it("切赛季时 tableType/recency/venue 都要带着走", () => {
    render(
      <SeasonSwitcher
        leagueId="47"
        section="standings"
        seasons={SEASONS}
        tableType="xg"
      />,
    );
    fireEvent.change(screen.getByLabelText("选择赛季"), { target: { value: "2024/2025" } });
    expect(push).toHaveBeenCalledWith("/league/47/standings?season=2024%2F2025&table_type=xg");
  });

  it("team-stats 页切赛季时 recency/venue 都要带着走", () => {
    render(
      <SeasonSwitcher leagueId="47" section="team-stats" seasons={SEASONS} recency={5} venue="home" />,
    );
    fireEvent.change(screen.getByLabelText("选择赛季"), { target: { value: "2023/2024" } });
    expect(push).toHaveBeenCalledWith(
      "/league/47/team-stats?season=2023%2F2024&recency=5&venue=home",
    );
  });

  it("切回「自动」时链接不带 season 参数", () => {
    render(
      <SeasonSwitcher leagueId="47" section="team-stats" seasons={SEASONS} selected="2024/2025" />,
    );
    fireEvent.change(screen.getByLabelText("选择赛季"), { target: { value: "" } });
    expect(push).toHaveBeenCalledWith("/league/47/team-stats");
  });

  it("选中的赛季不在可选列表里(selectedUnknown)时显示提示文案", () => {
    render(
      <SeasonSwitcher
        leagueId="47"
        section="team-stats"
        seasons={SEASONS}
        selected="2026/2027"
        resolved="2025/2026"
      />,
    );
    expect(screen.getByText(/该赛季本页无数据/)).toBeTruthy();
    expect(screen.getByText(/已展示 2025\/2026/)).toBeTruthy();
  });

  it("赛季在列表里时不显示 selectedUnknown 提示", () => {
    render(
      <SeasonSwitcher leagueId="47" section="team-stats" seasons={SEASONS} selected="2024/2025" />,
    );
    expect(screen.queryByText(/该赛季本页无数据/)).toBeNull();
  });

  it("inline=true 时仍然渲染同一个下拉(供调用方塞进自己的行)", () => {
    render(
      <SeasonSwitcher leagueId="47" section="team-stats" seasons={SEASONS} inline />,
    );
    expect(screen.getByLabelText("选择赛季")).toBeTruthy();
  });
});
