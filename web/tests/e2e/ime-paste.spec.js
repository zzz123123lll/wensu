import { test, expect } from '@playwright/test';

// WEN-008 红测：中文 IME 与最小安全粘贴。

const ARTICLE = {
  id: 7, project_id: 1, title: '测试稿', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '原始内容', attrs: {} }],
  created_at: '', updated_at: '',
};

async function openArticle(page) {
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '测试稿', updated_at: '' }] }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: ARTICLE }));
  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');
}

test('IME 候选确认 Enter 不拆块', async ({ page }) => {
  await openArticle(page);
  const before = await page.locator('.blk.edit').count();
  await page.evaluate(() => {
    const art = document.getElementById('article');
    const block = art.querySelector('.blk.edit');
    art.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '' }));
    block.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter', bubbles: true, cancelable: true, isComposing: true,
    }));
    art.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '中文候选' }));
  });
  await page.waitForTimeout(200);
  const after = await page.locator('.blk.edit').count();
  expect(after).toBe(before); // composition 中 Enter 不拆块
});

test('危险 HTML 粘贴只保留纯文本', async ({ page }) => {
  await openArticle(page);
  await page.evaluate(() => {
    const art = document.getElementById('article');
    const dt = new DataTransfer();
    dt.setData('text/html', '<img src=x onerror=alert(1)><script>window.__x=1</script><b>粗体</b>');
    dt.setData('text/plain', '纯文本内容');
    art.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
  });
  await page.waitForTimeout(200);
  // 无可执行节点
  const bad = await page.evaluate(() => document.querySelectorAll('#article script, #article img, #article iframe, #article [onerror]').length);
  expect(bad).toBe(0);
  // 纯文本进入正文
  const text = await page.evaluate(() => document.getElementById('article').textContent);
  expect(text).toContain('纯文本内容');
  expect(text).not.toContain('<b>');
});
