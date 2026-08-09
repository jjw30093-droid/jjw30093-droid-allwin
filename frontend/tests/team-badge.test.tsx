import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TeamBadge, teamInitials } from "@/components/teams/TeamBadge";

afterEach(cleanup);

describe("TeamBadge", () => {
  it("renders a same-origin crest with fixed dimensions and decorative semantics", () => {
    const { container } = render(
      <TeamBadge
        teamName="瓦勒伦加"
        crestUrl="/api/v1/media/team-crests/fotmob/8007.png?v=123456789abc"
        size={48}
      />,
    );
    const image = container.querySelector("img");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toContain("/api/v1/media/team-crests/");
    expect(image?.getAttribute("alt")).toBe("");
    expect(image?.getAttribute("width")).toBe("48");
    expect(screen.getByTestId("team-badge-image").className).toMatch(/size48/);
  });

  it("uses a stable shield fallback for null crest URLs", () => {
    render(<TeamBadge teamName="Hamarkameratene" crestUrl={null} size={24} />);
    const fallback = screen.getByTestId("team-badge-fallback");
    expect(fallback.textContent).toBe("HA");
    expect(fallback.getAttribute("aria-hidden")).toBe("true");
    expect(fallback.className).toMatch(/size24/);
  });

  it("replaces a failed image without leaving a broken icon", () => {
    const { container } = render(
      <TeamBadge
        teamName="博德闪耀"
        crestUrl="/api/v1/media/team-crests/fotmob/8402.png?v=123456789abc"
        size={32}
      />,
    );
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("team-badge-fallback").textContent).toBe("博德");
  });

  it("supports an accessible standalone image and fallback", () => {
    const { rerender } = render(
      <TeamBadge
        teamName="利勒斯特罗姆"
        crestUrl="/api/v1/media/team-crests/fotmob/8476.png?v=123456789abc"
        decorative={false}
        accessibleName="利勒斯特罗姆俱乐部队徽"
        size={40}
      />,
    );
    expect(
      screen.getByRole("img", { name: "利勒斯特罗姆俱乐部队徽" }),
    ).not.toBeNull();
    rerender(
      <TeamBadge
        teamName="利勒斯特罗姆"
        crestUrl={null}
        decorative={false}
        size={40}
      />,
    );
    expect(
      screen.getByRole("img", { name: "利勒斯特罗姆队徽" }),
    ).not.toBeNull();
  });

  it("uses one component tree across light and dark themes", () => {
    document.documentElement.dataset.theme = "light";
    const { container } = render(
      <TeamBadge
        teamName="Vålerenga"
        crestUrl="/api/v1/media/team-crests/fotmob/8007.png?v=123456789abc"
        size={56}
      />,
    );
    const before = container.firstElementChild;
    document.documentElement.dataset.theme = "dark";
    expect(container.firstElementChild).toBe(before);
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(before?.className).toMatch(/size56/);
  });

  it("builds deterministic Latin and CJK initials", () => {
    expect(teamInitials("Manchester City")).toBe("MC");
    expect(teamInitials("瓦勒伦加")).toBe("瓦勒");
    expect(teamInitials(" ")).toBe("队");
  });
});
