import { test, expect } from '@playwright/test';

// P1-④ Ask 流式：前端走 /api/ai/ask/stream（token 事件 + result），不请求非流式端点。

const ARTICLE = {
  id: 32, project_id: 1, title: '流式测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '正文段落。' }],
};

const NDJSON = [
  JSON.stringify({ type: 'token', text: '流式' }),
  JSON.stringify({ type: 'token', text: '回答' }),
  JSON.stringify({ type: 'token', text: '内容' }),
  JSON.stringify({ type: 'result', reply: '流式回答内容', model: 'deepseek-x', ask_id: 11 }),
].join('\n') + '\n';

test('Ask 走流式端点：回答呈现且不回退非流式', async ({ page }) => {
  let nonStreamHits = 0;
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/ai/ask/stream', r => r.fulfill({ status: 200, contentType: 'application/x-ndjson', body: NDJSON }));
  await page.route('**/api/ai/ask', r => { nonStreamHits += 1; return r.fulfill({ json: { reply: '不该走这里' } }); });
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  await page.fill('#ask-input', '帮我看看这段');
  await page.click('#ask-send');

  await expect(page.locator('#cardflow')).toContainText('流式回答内容', { timeout: 5000 });
  await expect(page.locator('#cardflow')).toContainText('deepseek-x'); // 模型名
  await expect(page.locator('#cardflow')).toContainText('保存为素材');
  expect(nonStreamHits).toBe(0); // 未回退非流式
});
