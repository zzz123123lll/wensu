import { test, expect } from '@playwright/test';

// f6：AI 在场——反复删改同块 3 次 → 低打扰提示卡；不自动调用模型。

const ARTICLE = {
  id: 22, project_id: 1, title: '在场测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '初始内容' }],
};

test('同块快速编辑 3 次 → 出现低打扰提示卡（不调模型）', async ({ page }) => {
  let aiCalls = 0;
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => {
    if (r.request().method() === 'PUT') return r.fulfill({ json: { ok: true, version: 2 } });
    return r.fulfill({ json: ARTICLE });
  });
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/ai/**', r => { aiCalls++; return r.fulfill({ json: { candidates: [] } }); });
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // 同块快速编辑 3 次
  const blk = page.locator('#article .blk.edit');
  for (const t of ['第一次改', '第二次改', '第三次改']) {
    await blk.click();
    await page.keyboard.press('Control+a');
    await page.keyboard.type(t);
  }

  // 提示卡出现（低打扰，prepend 到 cardflow）
  await expect(page.locator('.presence-hint')).toContainText('改了好几次', { timeout: 3000 });
  await expect(page.locator('.presence-hint')).toContainText('改写这段');
  // 关键：没有自动调用任何 AI 端点
  expect(aiCalls).toBe(0);
});
