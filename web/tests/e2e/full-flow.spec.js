import { test, expect } from '@playwright/test';

// WEN-016 全链路：建项目 → 建稿 → 写 → 自动保存 → 切稿不串 → AI 改写接受 → 刷新恢复。

test('全链路：建项目→建稿→写→切稿→AI改写→刷新', async ({ page }) => {
  const puts = [];
  let projects = [];
  let articles = [];
  let articleStore = {};
  let nextAid = 7;

  await page.route('**/api/projects', async r => {
    if (r.request().method() === 'POST') {
      const body = r.request().postDataJSON();
      projects = [...projects, { id: 1, name: body.name }];
      return r.fulfill({ json: { id: 1, name: body.name } });
    }
    return r.fulfill({ json: projects });
  });
  await page.route('**/api/projects/1/articles', async r => {
    if (r.request().method() === 'POST') {
      const body = r.request().postDataJSON();
      const id = ++nextAid;
      articles = [...articles, { id, title: body.title, updated_at: '' }];
      articleStore[id] = { id, project_id: 1, title: body.title, version: 1, blocks: [], created_at: '', updated_at: '' };
      return r.fulfill({ json: { id, title: body.title } });
    }
    return r.fulfill({ json: articles });
  });
  await page.route('**/api/articles/*', async r => {
    const aid = +new URL(r.request().url()).pathname.split('/').pop();
    if (r.request().method() === 'PUT') {
      const body = r.request().postDataJSON();
      puts.push({ aid, body });
      const cur = articleStore[aid] || { version: 0 };
      articleStore[aid] = { ...cur, blocks: body.blocks, version: cur.version + 1 };
      return r.fulfill({ json: { ok: true, article_id: aid, version: cur.version + 1, blocks_hash: 'h' } });
    }
    return r.fulfill({ json: articleStore[aid] || { id: aid, project_id: 1, title: '新稿', version: 1, blocks: [], created_at: '', updated_at: '' } });
  });
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  // AI 改写 mock
  await page.route('**/api/ai/rewrite', r => r.fulfill({ json: {
    candidates: [{ label: '方案一', text: 'AI 改写后的内容' }],
    anchor: { article_id: 7, target_block_id: null, selection: null },
  } }));

  await page.goto('http://127.0.0.1:8790/');

  // 1. 建项目
  await page.click('#btn-new-proj');
  await page.fill('.name-input input', '随笔');
  await page.keyboard.press('Enter');
  await expect(page.locator('.proj')).toContainText('随笔');

  // 2. 展开 → 建草稿 → 打开空白稿
  await page.click('.proj');
  await page.click('.doc[data-new]');
  await page.fill('.name-input input', '第一篇');
  await page.keyboard.press('Enter');
  await expect(page.locator('#doc-title')).toHaveText('第一篇');

  // 3. 写作 → 自动保存
  const block = page.locator('#article .blk.edit').first();
  await block.click();
  await page.keyboard.type('这是第一篇的内容，测试自动保存。');
  await page.waitForTimeout(3000);
  expect(puts.some(p => p.body.blocks[0].text.includes('自动保存'))).toBe(true);

  // 4. AI 改写 → 接受（ai_rewrite reason）
  await page.click('#tool-rw');
  await page.waitForSelector('.ai-card .opt');
  await page.click('.ai-card [data-x="acc"]');
  await page.waitForTimeout(1500);
  const rewritePut = puts.filter(p => p.body.change_reason === 'ai_rewrite');
  expect(rewritePut.length).toBeGreaterThan(0);
  expect(rewritePut.at(-1).body.blocks[0].text).toContain('AI 改写后的内容');

  // 5. 刷新恢复（服务端存储了 blocks）
  await page.reload();
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');
  await expect(page.locator('#article .blk.edit').first()).toContainText('AI 改写后的内容');
});
