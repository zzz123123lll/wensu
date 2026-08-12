import { test, expect } from '@playwright/test';

// WEN-014 红测：窄屏抽屉布局（375/768 写作区可达、抽屉互斥、选择后自动关闭）。

const MOCK = {
  projects: [{ id: 1, name: '随笔' }],
  articles: [{ id: 7, title: '第一篇草稿标题', updated_at: '' }],
  article: {
    id: 7, project_id: 1, title: '第一篇', version: 1,
    blocks: [
      { id: 'b1', type: 'paragraph', text: '第一段内容比较长，用来验证窄屏下写作区是否可达。', attrs: {} },
    ],
    created_at: '', updated_at: '',
  },
  settings: { configured: true, base_url: 'https://x', model: 'm', has_key: true },
};

async function mockRoutes(page) {
  await page.route('**/api/projects', r => r.fulfill({ json: MOCK.projects }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: MOCK.articles }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: MOCK.article }));
  await page.route('**/api/settings', r => r.fulfill({ json: MOCK.settings }));
}

test('375 窄屏：写作区可达，抽屉可开可关', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockRoutes(page);
  await page.goto('http://127.0.0.1:8790/');
  // 写作区可见（不消失、不竖排挤没）
  const docVisible = await page.locator('#doc').isVisible();
  expect(docVisible).toBe(true);
  // 抽屉按钮可见
  await expect(page.locator('#btn-drawer-proj')).toBeVisible();
  await expect(page.locator('#btn-drawer-helper')).toBeVisible();
  // 打开项目抽屉 → 侧栏滑入
  await page.click('#btn-drawer-proj');
  await expect(page.locator('body')).toHaveClass(/drawer-proj/);
  // 打开草稿 → 抽屉自动关
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');
  await expect(page.locator('body')).not.toHaveClass(/drawer-proj/);
  // 助手抽屉互斥可开
  await page.click('#btn-drawer-helper');
  await expect(page.locator('body')).toHaveClass(/drawer-helper/);
  // Esc 关闭
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).not.toHaveClass(/drawer-helper/);
});

test('768 平板：写作区常驻，侧栏默认收起', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await mockRoutes(page);
  await page.goto('http://127.0.0.1:8790/');
  // 侧栏是抽屉（默认隐藏）
  await expect(page.locator('#sidebar')).not.toBeInViewport();
  // 写作区可见
  await expect(page.locator('#doc')).toBeVisible();
  await expect(page.locator('#doc-inner h1')).toBeVisible(); // 干净起始页
});

test('1280 桌面：三栏都在', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockRoutes(page);
  await page.goto('http://127.0.0.1:8790/');
  await expect(page.locator('#sidebar')).toBeVisible();
  await expect(page.locator('#tools')).toBeVisible();
  await expect(page.locator('#doc')).toBeVisible();
  // 抽屉按钮隐藏
  await expect(page.locator('#btn-drawer-proj')).toBeHidden();
});
