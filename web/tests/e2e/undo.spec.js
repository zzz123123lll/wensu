import { test, expect } from '@playwright/test';

// WEN-023：AI 接受可撤销（Ctrl+Z）；有选区时精确替换选中文字。

const ARTICLE = {
  id: 7, project_id: 1, title: '撤销测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '原始段落内容在此处', attrs: {} }],
  created_at: '', updated_at: '',
};

async function open(page) {
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '撤销测试', updated_at: '' }] }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/ai/rewrite', r => r.fulfill({ json: {
    candidates: [{ label: '方案一', text: 'AI改写内容' }],
    anchor: { article_id: 7, target_block_id: null, selection: null },
  } }));
  await page.route('**/api/articles/7/citations', r => r.fulfill({ json: { citations: [] } }));
  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');
}

test('接受改写后 Ctrl+Z 撤销恢复原文本', async ({ page }) => {
  await open(page);
  await page.click('#tool-rw');
  await page.waitForSelector('.ai-card .opt');
  await page.click('.ai-card [data-x="acc"]');
  await expect(page.locator('#article .blk.edit').first()).toContainText('AI改写内容');
  // 撤销
  await page.keyboard.press('Control+z');
  await page.waitForTimeout(400);
  await expect(page.locator('#article .blk.edit').first()).toContainText('原始段落内容在此处');
  await expect(page.locator('#article .blk.edit').first()).not.toContainText('AI改写内容');
});

test('有选区时只替换选中文字', async ({ page }) => {
  await open(page);
  // 选中"原始"两个字（前 2 个字符）
  await page.evaluate(() => {
    const block = document.querySelector('#article .blk.edit');
    const r = document.createRange();
    r.setStart(block.firstChild, 0);
    r.setEnd(block.firstChild, 2);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
  });
  await page.click('#tool-rw');
  await page.waitForSelector('.ai-card .opt');
  await page.click('.ai-card [data-x="acc"]');
  await page.waitForTimeout(400);
  // 只前 2 字被替换，其余保留
  await expect(page.locator('#article .blk.edit').first()).toContainText('AI改写内容段落内容在此处');
});
