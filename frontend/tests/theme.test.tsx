import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "@/components/ThemeToggle";

const listeners = new Set<() => void>();

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.dataset.theme = "light";
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addEventListener: (_type: string, listener: () => void) =>
        listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) =>
        listeners.delete(listener),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(() => {
  cleanup();
  listeners.clear();
});

describe("ThemeToggle", () => {
  it("在深浅模式间切换并持久化选择", async () => {
    render(<ThemeToggle />);
    const button = await screen.findByRole("button", {
      name: "切换为深色模式",
    });

    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(window.localStorage.getItem("allwin-theme")).toBe("dark");
    expect(button.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("allwin-theme")).toBe("light");
    expect(button.getAttribute("aria-pressed")).toBe("false");
  });
});
