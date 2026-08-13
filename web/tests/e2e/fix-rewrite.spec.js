import { test, expect } from '@playwright/test';

// dogfood Bug#3 回归：AI 候选整句改写时，接受后正文不得残留拼接断裂。

const ARTICLE = {
  id: 21, project_id: 1, title: '断裂回归', version: 1,
  blocks: [
    { id: 'b1', type: 'paragraph', text: '这是第一句。这是第二句，中间部分需要改写。这是第三句。' },
  ],
};

test('选中句子中段 → 接受整句候选 → 无残留拼接', async ({ page }) => {
  let puts = [];
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => {
    if (r.request().method() === 'PUT') {
      const body = JSON.parse(r.request().postData() || '{}');
      puts.push({ body });
      return r.fulfill({ json: { ok: true, version: 2 } });
    }
    return r.fulfill({ json: ARTICLE });
  });
  await page.route('**/api/articles/' + ARTICLE.id + '/save', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/ai/rewrite', r => r.fulfill({
    json: { candidates: [{ label: '方案一', text: '这是第二句，改成了全新的整句表述。' }] },
  }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // 选中「中间部分需要改写」（句子中间的一段）
  await page.evaluate(() => {
    const block = document.querySelector('#article .blk.edit');
    const raw = block.textContent;
    const start = raw.indexOf('中间部分需要改写');
    const range = document.createRange();
    range.setStart(block.firstChild, start);
    range.setEnd(block.firstChild, start + '中间部分需要改写'.length);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  });

  // 点改写 → 接受方案一
  await page.click('#tool-rw');
  await page.waitForSelector('#article .ai-card .btn-p');
  await page.click('#article .ai-card [data-x="acc"]');

  // 关键断言：正文无断裂残留（原文片段不得与新句子拼在一起）
  const text = await page.evaluate(() => document.querySelector('#article .blk.edit').textContent);
  expect(text).toContain('这是第一句。');
  expect(text).toContain('这是第三句。');
  expect(text).toContain('这是第二句，改成了全新的整句表述。');
  expect(text).not.toContain('中间部分需要改写'); // 旧选区文字完全被句子级替换覆盖
  expect(text).not.toContain('……'); // 无乱码残留
});
