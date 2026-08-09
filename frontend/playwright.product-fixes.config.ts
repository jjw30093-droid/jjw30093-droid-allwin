import { defineConfig } from "@playwright/test";

const PREVIEW = process.env.ALLWIN_PRODUCT_PREVIEW_URL ?? "http://127.0.0.1:3600";

export default defineConfig({
  testDir: "./e2e-product-fixes",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: PREVIEW,
  },
});
