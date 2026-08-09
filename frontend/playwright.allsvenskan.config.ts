import { defineConfig } from "@playwright/test";

// 瑞典超(Allsvenskan)接入专用 E2E 验收:独立于 playwright.config.ts(8010/3010,
// 每次用 seed_e2e.py 重建合成数据)。这里指向真实 FotMob/NowGoal ingest 产出的
// 隔离实验库副本(ALLWIN_ALLSVENSKAN_PW_DATA_DIR,由外部脚本设置,绝不指向真实
// data/*.db),端口 8200/3200,不占用 3000/8000/8010/3010。
const API_PORT = 8200;
const WEB_PORT = 3200;
const API = `http://127.0.0.1:${API_PORT}`;
const WEB = `http://127.0.0.1:${WEB_PORT}`;
const DATA_DIR = process.env.ALLWIN_ALLSVENSKAN_PW_DATA_DIR;
if (!DATA_DIR) {
  throw new Error(
    "ALLWIN_ALLSVENSKAN_PW_DATA_DIR 未设置——瑞典超 E2E 必须显式指向隔离实验库副本,不得回退到默认/真实数据目录。",
  );
}

export default defineConfig({
  testDir: "./e2e-allsvenskan",
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
        `cd .. && ALLWIN_DATA_DIR=${DATA_DIR} .venv/bin/python -m tests.e2e.seed_allsvenskan_pw && ` +
        `ALLWIN_DATA_DIR=${DATA_DIR} APP_ENV=development ` +
        `WECHAT_AUTH_PROVIDER=mock WECHAT_AUTH_ENABLED=1 ` +
        `PUBLIC_BASE_URL=${API} FRONTEND_BASE_URL=${WEB} ` +
        `ALLOWED_ORIGINS=${WEB},http://localhost:${WEB_PORT} ` +
        `.venv/bin/python -m uvicorn backend.api.app:app --host 127.0.0.1 --port ${API_PORT}`,
      url: `${API}/healthz`,
      reuseExistingServer: false,
      timeout: 90_000,
    },
    {
      command: `NEXT_PUBLIC_API_BASE=${API} npm run build && NEXT_PUBLIC_API_BASE=${API} INTERNAL_API_BASE=${API} npx next start -p ${WEB_PORT}`,
      url: WEB,
      reuseExistingServer: false,
      timeout: 300_000,
    },
  ],
});
