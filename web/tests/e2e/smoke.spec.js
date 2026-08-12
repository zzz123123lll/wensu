import { test, expect } from '@playwright/test';

// 冒烟：打开草稿 → 编辑 → 自动保存，保存请求携带正确 body。
// 所有 API 由浏览器端 route mock 提供（fake transport），不访问真实后端。

const MOCK = {
  projects: [{ id: 1, name: '随笔' }],
  articles: [{ id: 7, title: '第一篇', updated_at: '2026-01-01T00:00:00' }],
  article: {
    id: 7, project_id: 1, title: '第一篇', version: 1,
    blocks: [{ id: 'b1', type: 'paragraph', text: '你好', attrs: {} }],
    created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00',
  },
  settings: { configured: true, base_url: 'https://api.example.com/v1', model: 'mock', has_key: true },
  insight: { insight: { summary: 's', gap: 'g' }, suggestions: [] },
};

test('打开草稿、编辑并自动保存', async ({ page }) => {
  const savedBodies = [];

  await page.route('**/api/projects', r => r.fulfill({ json: MOCK.projects }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: MOCK.articles }));
  await page.route('**/api/articles/7', async r => {
    if (r.request().method() === 'PUT') {
      savedBodies.push(r.request().postDataJSON());
      return r.fulfill({ json: { ok: true, article_id: 7, version: 2, blocks_hash: 'h' } });
    }
    return r.fulfill({ json: MOCK.article });
  });
  await page.route('**/api/settings', r => r.fulfill({ json: MOCK.settings }));
  await page.route('**/api/ai/insight', r => r.fulfill({ json: MOCK.insight }));

  await page.goto('http://127.0.0.1:8790/');
  await expect(page.getByRole('heading', { name: '今天想写点什么？' })).toBeVisible();

  // 展开项目 → 打开草稿
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await expect(page.locator('#doc-title')).toHaveText('第一篇');

  // 编辑第一个 Block
  const block = page.locator('#article .blk.edit').first();
  await block.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type('你好世界，这是新内容。');

  // 等待自动保存（防抖 1.2s + 余量）
  await page.waitForTimeout(3000);
  expect(savedBodies.length).toBeGreaterThan(0);
  expect(savedBodies.at(-1).blocks[0].text).toContain('你好世界');
  // Block ID 稳定：所有保存请求中第一个块 ID 相同（不每次重新生成）
  const ids = savedBodies.map(b => b.blocks[0].id);
  expect(new Set(ids).size).toBe(1);
});

test('干净打开不触发洞察（默认不自动调用模型）', async ({ page }) => {
  let insightCalls = 0;
  await page.route('**/api/projects', r => r.fulfill({ json: MOCK.projects }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: MOCK.articles }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: MOCK.article }));
  await page.route('**/api/settings', r => r.fulfill({ json: MOCK.settings }));
  await page.route('**/api/ai/insight', r => { insightCalls++; return r.fulfill({ json: MOCK.insight }); });

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForTimeout(1000);
  expect(insightCalls).toBe(0); // 打开草稿不自动调模型
});
