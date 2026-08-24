import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";

afterEach(cleanup);

describe("PlayerAvatar", () => {
  it("renders a hotlinked FotMob avatar with fixed dimensions and decorative semantics", () => {
    const { container } = render(
      <PlayerAvatar playerId={1077894} playerName="Jude Bellingham" shirtNumber="5" size={48} />,
    );
    const image = container.querySelector("img");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toContain(
      "images.fotmob.com/image_resources/playerimages/1077894.png",
    );
    expect(image?.getAttribute("alt")).toBe("");
    expect(image?.getAttribute("width")).toBe("48");
    expect(screen.getByTestId("player-avatar-image").className).toMatch(/size48/);
  });

  it("accepts a string playerId(真实首发阵型图的 player_id 是字符串,预测阵容的 id 是数字)", () => {
    const { container } = render(
      <PlayerAvatar playerId="1077894" playerName="Jude Bellingham" size={32} />,
    );
    expect(container.querySelector("img")?.getAttribute("src")).toContain(
      "/playerimages/1077894.png",
    );
  });

  it("replaces a failed image with the shirt number, not a broken icon", () => {
    const { container } = render(
      <PlayerAvatar playerId={999999} playerName="测试球员" shirtNumber="23" size={32} />,
    );
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("player-avatar-fallback").textContent).toBe("23");
  });

  it("falls back to the first character of the name when there is no shirt number", () => {
    const { container } = render(
      <PlayerAvatar playerId={999999} playerName="测试球员" size={32} />,
    );
    fireEvent.error(container.querySelector("img")!);
    expect(screen.getByTestId("player-avatar-fallback").textContent).toBe("测");
  });

  it("supports an accessible standalone image and fallback", () => {
    const { container } = render(
      <PlayerAvatar
        playerId={1077894}
        playerName="Jude Bellingham"
        decorative={false}
        accessibleName="Jude Bellingham 头像"
        size={40}
      />,
    );
    expect(screen.getByRole("img", { name: "Jude Bellingham 头像" })).not.toBeNull();
    // accessibleName 是常量 prop,加载失败切到文字兜底后应该保持同一个
    // 无障碍名称——不应该切回默认模板(那只在没传 accessibleName 时才用)。
    fireEvent.error(container.querySelector("img")!);
    expect(screen.getByRole("img", { name: "Jude Bellingham 头像" })).not.toBeNull();
  });

  it("uses one component tree across light and dark themes", () => {
    document.documentElement.dataset.theme = "light";
    const { container } = render(
      <PlayerAvatar playerId={1077894} playerName="Jude Bellingham" size={56} />,
    );
    const before = container.firstElementChild;
    document.documentElement.dataset.theme = "dark";
    expect(container.firstElementChild).toBe(before);
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(before?.className).toMatch(/size56/);
  });
});
