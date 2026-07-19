import { defineConfig } from "@playwright/test";

// E2E:独立 data/e2e 数据目录(tests/e2e/seed_e2e.py 每次重建),
// API 127.0.0.1:8010 + Next dev 127.0.0.1:3010。
// 同 host 跨端口属同站:SameSite=Lax cookie 可随 credentialed fetch 发送。
const API_PORT = 8010;
const WEB_PORT = 3010;
const API = `http://127.0.0.1:${API_PORT}`;
const WEB = `http://127.0.0.1:${WEB_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: WEB,
  },
  webServer: [
    {
      command:
        `cd .. && .venv/bin/python -m tests.e2e.seed_e2e && ` +
        `ALLWIN_DATA_DIR=data/e2e APP_ENV=development ` +
        `PUBLIC_BASE_URL=${API} FRONTEND_BASE_URL=${WEB} ` +
        `ALLOWED_ORIGINS=${WEB},http://localhost:${WEB_PORT} ` +
        `.venv/bin/python -m uvicorn backend.api.app:app --host 127.0.0.1 --port ${API_PORT}`,
      url: `${API}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
    },
    {
      // 生产构建跑 E2E:dev(Turbopack)在自动化浏览器下存在水合停滞问题,
      // 且 NEXT_PUBLIC_* 是构建期内联,必须带着 E2E 的 API 地址 build。
      command: `NEXT_PUBLIC_API_BASE=${API} npm run build && NEXT_PUBLIC_API_BASE=${API} npx next start -p ${WEB_PORT}`,
      url: WEB,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
  ],
});
