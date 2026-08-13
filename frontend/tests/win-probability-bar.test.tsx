import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WinProbabilityBar } from "@/components/matches/WinProbabilityBar";

describe("WinProbabilityBar", () => {
  it("无数据时整条不占位(不画假等分,不补 0)", () => {
    const { container } = render(<WinProbabilityBar probability={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("undefined 同样不占位", () => {
    const { container } = render(<WinProbabilityBar probability={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("有数据时三段都四舍五入成整数百分比,并带 aria-label 兜底可读性", () => {
    render(
      <WinProbabilityBar
        probability={{ p_home: 0.4065, p_draw: 0.3339, p_away: 0.2597, observed_at: "2026-08-12T00:34:47Z" }}
      />,
    );
    expect(screen.getByText("41%")).not.toBeNull();
    expect(screen.getByText("33%")).not.toBeNull();
    expect(screen.getByText("26%")).not.toBeNull();
    expect(
      screen.getByLabelText("胜平负概率:主胜 41%,平局 33%,客胜 26%"),
    ).not.toBeNull();
  });

  it("真实审计样本(马竞 vs 马拉加)对拍:73/18/9", () => {
    render(
      <WinProbabilityBar
        probability={{ p_home: 0.7259, p_draw: 0.1797, p_away: 0.0944, observed_at: "2026-08-12T01:39:50Z" }}
      />,
    );
    expect(screen.getByText("73%")).not.toBeNull();
    expect(screen.getByText("18%")).not.toBeNull();
    expect(screen.getByText("9%")).not.toBeNull();
  });
});
