import { test, expect } from '@playwright/test';

// P1-⑤ 选区浮层：选中文字 → 就地工具条（改写/去AI味/搜索/核验）→ 点击后隐藏。

const ARTICLE = {
  id: 33, project_id: 1, title: '选区浮层测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '这是一段可以被选中的正文内容。' }],
};

async function selectText(page, start, end) {
  await page.evaluate(([s, e]) => {
    const blk = document.querySelector('#article .blk.edit');
    const range = document.createRange();
    range.setStart(blk.firstChild, s);
    range.setEnd(blk.firstChild, e);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  }, [start, end]);
}

test('选中文字出现浮层：搜索/核验就地执行并隐藏', async ({ page }) => {
  const hits = { search: 0, check: 0, rewrite: 0 };
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/ai/search', r => {
    hits.search += 1;
    return r.fulfill({
      contentType: 'application/x-ndjson',
      body: JSON.stringify({ type: 'stage', stage: 'fetching' }) + '\n'
        + JSON.stringify({ type: 'result', results: [{ title: '检索到的资料', url: 'https://example.com/a', snippet: '摘要', source: 'web' }] }) + '\n',
    });
  });
  await page.route('**/api/ai/check', r => {
    hits.check += 1;
    return r.fulfill({ json: { status: 'doubt', reason: '无法证实', suggestion: '', evidence: [] } });
  });
  await page.route('**/api/ai/rewrite/stream', r => {
    hits.rewrite += 1;
    return r.fulfill({
      contentType: 'application/x-ndjson',
      body: JSON.stringify({ type: 'result', candidates: [{ label: '方案一', text: '改写后' }] }) + '\n',
    });
  });
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // 初始：浮层隐藏
  await expect(page.locator('#selbar')).toBeHidden();

  // 选中 → 浮层出现（四个工具）
  await selectText(page, 0, 6);
  await expect(page.locator('#selbar')).toBeVisible();
  await expect(page.locator('#selbar')).toContainText('改写');
  await expect(page.locator('#selbar')).toContainText('搜索');
  await expect(page.locator('#selbar')).toContainText('核验');

  // 点「搜索」→ 就地搜索卡出现，浮层隐藏
  await page.click('#selbar [data-t="search"]');
  await expect(page.locator('.ai-card')).toContainText('检索到的资料', { timeout: 5000 });
  await expect(page.locator('#selbar')).toBeHidden();
  expect(hits.search).toBe(1);

  // 再选中 → 点「核验」
  await selectText(page, 0, 8);
  await expect(page.locator('#selbar')).toBeVisible();
  await page.click('#selbar [data-t="check"]');
  await expect(page.locator('#selbar')).toBeHidden();
  expect(hits.check).toBe(1);

  // 点空白处 → 选区消失，浮层保持隐藏
  await page.mouse.click(10, 200);
  await expect(page.locator('#selbar')).toBeHidden();
});
