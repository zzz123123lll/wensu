import { test, expect } from '@playwright/test';

// P2-⑧ 剪藏：素材库粘贴网址 → 收藏为素材 → 列表刷新可见。

const ARTICLE = {
  id: 35, project_id: 1, title: '剪藏测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '正文。' }],
};

test('剪藏网址入素材库', async ({ page }) => {
  const materials = [];
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/materials?*', r => r.fulfill({ json: { materials } }));
  await page.route('**/api/projects/1/clip', r => {
    materials.push({ id: 1, project_id: 1, source_id: 10, title: '剪藏的网页', content: '网页正文内容', tags: ['剪藏'], source_title: '剪藏的网页', url: 'https://example.com/a', metadata: {} });
    return r.fulfill({ json: { material_id: 1, source_id: 10, title: '剪藏的网页', chars: 6 } });
  });
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  await page.click('#btn-materials');
  await expect(page.locator('#materials-modal')).toBeVisible();
  await expect(page.locator('#mat-list')).toContainText('还没有素材');

  await page.fill('#mat-clip-url', 'https://example.com/a');
  await page.click('#mat-clip-btn');

  await expect(page.locator('#toast')).toContainText('已收藏为素材');
  await expect(page.locator('#mat-list')).toContainText('剪藏的网页');
  await expect(page.locator('#mat-list')).toContainText('剪藏'); // 标签
});
