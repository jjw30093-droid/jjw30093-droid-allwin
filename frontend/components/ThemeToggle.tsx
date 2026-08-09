"use client";

import { useEffect, useState } from "react";
import styles from "./ThemeToggle.module.css";

type Theme = "light" | "dark";

const STORAGE_KEY = "allwin-theme";
const CHANGE_EVENT = "allwin-theme-change";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function savedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: theme }));
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const syncFromDocument = () => setTheme(currentTheme());
    const syncFromSystem = () => {
      if (savedTheme() == null) applyTheme(systemTheme());
    };

    syncFromDocument();
    window.addEventListener(CHANGE_EVENT, syncFromDocument);
    media.addEventListener("change", syncFromSystem);
    return () => {
      window.removeEventListener(CHANGE_EVENT, syncFromDocument);
      media.removeEventListener("change", syncFromSystem);
    };
  }, []);

  const isDark = theme === "dark";

  const toggle = () => {
    const next: Theme = currentTheme() === "dark" ? "light" : "dark";
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 隐私模式下仍允许本次页面切换，只是不持久化。
    }
    applyTheme(next);
  };

  return (
    <button
      type="button"
      className={styles.toggle}
      onClick={toggle}
      aria-label={isDark ? "切换为浅色模式" : "切换为深色模式"}
      aria-pressed={isDark}
      data-testid="theme-toggle"
      title={isDark ? "浅色模式" : "深色模式"}
    >
      <svg className={styles.sun} viewBox="0 0 24 24" aria-hidden>
        <circle cx="12" cy="12" r="3.5" />
        <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4" />
      </svg>
      <svg className={styles.moon} viewBox="0 0 24 24" aria-hidden>
        <path d="M19.4 15.4A8 8 0 0 1 8.6 4.6 8 8 0 1 0 19.4 15.4Z" />
      </svg>
    </button>
  );
}
