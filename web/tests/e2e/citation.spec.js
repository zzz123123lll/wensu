import { test, expect } from '@playwright/test';

// WEN-017/018：搜索结果引用落库（真 Citation）+ 素材存入 + 编号 badge 渲染。

const ARTICLE = {
  id: 7, project_id: 1, title: '引用测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '需要引用来源的段落内容', attrs: {} }],
  created_at: '', updated_at: '',
};

test('引用落库 + badge 渲染 + 素材存入', async ({ page }) => {
  const citations = [];
  const materials = [];
  const sources = [];

  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '引用测试', updated_at: '' }] }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/ai/search', r => r.fulfill({ json: {
    results: [
      { title: '来源一', url: 'https://a.com', snippet: '摘要一', source: 'model' },
      { title: '来源二', url: 'https://b.com', snippet: '摘要二', source: 'web' },
    ],
    anchor: null,
  } }));
  await page.route('**/api/projects/1/sources', async r => {
    const body = r.request().postDataJSON();
    const id = sources.length + 1;
    sources.push({ id, ...body });
    return r.fulfill({ json: { id } });
  });
  await page.route('**/api/articles/7/citations', async r => {
    if (r.request().method() === 'POST') {
      const body = r.request().postDataJSON();
      citations.push(body);
      return r.fulfill({ json: { id: citations.length } });
    }
    return r.fulfill({ json: { citations: citations.map((c, i) => ({
      id: i + 1, ...c, status: 'active',
      source_title: sources.find(s => s.id === c.source_id)?.title || '来源',
      source_url: sources.find(s => s.id === c.source_id)?.url || '',
    })) } });
  });
  await page.route('**/api/projects/1/materials', async r => {
    const body = r.request().postDataJSON();
    materials.push(body);
    return r.fulfill({ json: { id: materials.length } });
  });

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');

  // 触发搜索
  await page.click('#tool-sr');
  await page.waitForSelector('.ai-card .res');

  // 引用第一条 → 落库 + badge
  await page.click('.ai-card .res >> nth=0 >> [data-x="cite"]');
  await page.waitForTimeout(600);
  expect(citations).toHaveLength(1);
  expect(citations[0].block_id).toBe('b1');
  expect(citations[0].source_id).toBe(1);
  await expect(page.locator('sup.cite').first()).toHaveText('[1]');

  // 素材存入
  await page.click('.ai-card .res >> nth=1 >> [data-x="save"]');
  await page.waitForTimeout(600);
  expect(materials).toHaveLength(1);
  expect(materials[0].title).toBe('来源二');

  // 刷新后 badge 从服务端恢复（编号按 citations 顺序）
  await page.reload();
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');
  await expect(page.locator('sup.cite').first()).toHaveText('[1]');
});
