import { test, expect } from '@playwright/test';

// 新手引导（轻量）与通用发布面板的界面闭环。

const ARTICLE = {
  id: 36, project_id: 1, title: '发布引导测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '正文段落。' }],
};

function baseRoutes(page, settingsConfigured = true) {
  return Promise.all([
    page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] })),
    page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] })),
    page.route('**/api/articles/' + ARTICLE.id, r => r.fulfill({ json: ARTICLE })),
    page.route('**/api/settings', r => r.fulfill({ json: { configured: settingsConfigured, base_url: 'https://x', model: 'm', has_key: settingsConfigured } })),
    page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } })),
    page.route('**/api/signals', r => r.fulfill({ json: { ok: true } })),
    page.route('**/api/prefs', r => r.fulfill({ json: { prefs: {} } })),
    page.route('**/api/profiles', r => r.fulfill({ json: { profiles: [] } })),
    page.route('**/api/bindings', r => r.fulfill({ json: { bindings: {} } })),
    page.route('**/api/review/packs', r => r.fulfill({ json: { packs: [] } })),
  ]);
}

test('新手引导：未配置模型时起步卡高亮第一步，点开设置', async ({ page }) => {
  await baseRoutes(page, false);
  await page.goto('http://127.0.0.1:8790/');
  await page.waitForSelector('#empty .ob-card');
  await expect(page.locator('.ob-step').first()).not.toHaveClass(/done/);
  await expect(page.locator('#empty .ob-card')).toContainText('配置你的模型');
  await expect(page.locator('#empty .ob-card')).toContainText('新建一篇草稿');
  await expect(page.locator('#empty .ob-card')).toContainText('选中文字召唤 AI');
  // 点第一步 → 打开设置弹窗
  await page.click('.ob-step[data-act="cfg"]');
  await expect(page.locator('#settings-modal')).toBeVisible();
  // 关闭按钮在长弹窗视口外，点背景关闭
  await page.click('#settings-modal', { position: { x: 10, y: 300 } });
  await expect(page.locator('#settings-modal')).toBeHidden();
  await page.click('.ob-close');
  await expect(page.locator('#empty .ob-card')).toHaveCount(0);
  await page.reload();
  await page.waitForSelector('#empty');
  await expect(page.locator('#empty .ob-card')).toHaveCount(0);
});

test('新手引导：已配置+已建稿+用过选区 → 三步全勾', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('wensu-has-drafts', '1');
    localStorage.setItem('wensu-sel-used', '1');
  });
  await baseRoutes(page, true);
  await page.goto('http://127.0.0.1:8790/');
  await page.waitForSelector('#empty .ob-card');
  await expect(page.locator('.ob-step.done')).toHaveCount(3);
});

test('发布面板：选目标发布成功 + 技巧卡', async ({ page }) => {
  await baseRoutes(page, true);
  await page.route('**/api/publish-targets', r => r.fulfill({
    json: { targets: [{ id: 1, name: '飞书群', kind: 'webhook', enabled: 1, summary: 'Webhook → open.feishu.cn', created_at: '' }] },
  }));
  await page.route('**/api/articles/' + ARTICLE.id + '/publish', r => r.fulfill({ json: { status: 'ok', message: 'HTTP 200', target: '飞书群' } }));
  await page.route('**/api/publish-logs', r => r.fulfill({
    json: { logs: [{ id: 1, target_name: '飞书群', fmt: 'markdown', status: 'ok', message: 'HTTP 200', created_at: '' }] },
  }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // 使用技巧卡
  await page.click('#btn-tips');
  await expect(page.locator('.tips-card')).toContainText('选中任何文字');

  // 发布面板
  await page.click('#btn-publish');
  await page.waitForSelector('.publish-card');
  await expect(page.locator('#pb-target')).toContainText('飞书群');
  await expect(page.locator('.publish-card')).toContainText('复制 HTML 到剪贴板');
  await page.click('#pb-go');
  await expect(page.locator('#toast')).toContainText('发布成功');
  await expect(page.locator('#pb-logs')).toContainText('飞书群');
});
