import { defineConfig } from '@playwright/test';

// 前端 E2E：静态服务 + 浏览器端 API mock（fake transport），
// 不访问真实后端、不访问公网。用系统 Chrome，避免下载浏览器。
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  use: {
    channel: 'chrome',
    headless: true,
  },
  webServer: {
    command: 'python -m http.server 8790 --bind 127.0.0.1',
    url: 'http://127.0.0.1:8790',
    reuseExistingServer: true,
    timeout: 15000,
  },
});
