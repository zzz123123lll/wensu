import { defineConfig } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LAUNCHER = path.join(__dirname, '..', 'scripts', 'e2e-server-launcher.js');

// 真实后端 E2E（Gate B E01~E12）：
// - 真实 FastAPI（127.0.0.1:8770，WENSU_DB=独立临时 SQLite）
// - 本地假 LLM 服务（127.0.0.1:8899，OpenAI 兼容，可控回复/错误）
// - 不调用真实模型、不访问公网、不触碰 data/workbench.db
// - 运行：npx playwright test --config playwright.config.real.js
export default defineConfig({
  testDir: './tests/e2e-real',
  timeout: 60000,
  workers: 1, // 共享同一个临时后端，串行执行
  use: {
    channel: 'chrome',
    headless: true,
    baseURL: 'http://127.0.0.1:8770',
  },
  webServer: [
    {
      command: `node "${LAUNCHER}" fake`,
      url: 'http://127.0.0.1:8899/health',
      reuseExistingServer: true,
      timeout: 20000,
    },
    {
      command: `node "${LAUNCHER}" app`,
      url: 'http://127.0.0.1:8770/api/session',
      reuseExistingServer: true,
      timeout: 20000,
      env: {
        WENSU_DB: 'e2e-tmp/gateb-e2e.db',
        WENSU_EXTRA_HOSTS: '127.0.0.1:8770',
        WENSU_EXTRA_ORIGINS: 'http://127.0.0.1:8770',
      },
    },
  ],
});
