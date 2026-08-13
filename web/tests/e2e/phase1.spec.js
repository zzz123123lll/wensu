import { test, expect } from '@playwright/test';

// 进化方案 阶段1：素材库 / 引用清单 / Ask 结果复用 的界面闭环。

const ARTICLE = {
  id: 31, project_id: 1, title: '阶段1测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '这是一个带引用的段落。' }],
};
const MATERIALS = [
  { id: 1, project_id: 1, source_id: 10, title: 'AI 市场规模数据', content: '2025 年市场规模 2000 亿美元', tags: ['数据'], source_title: '行业报告', url: 'https://example.com/r', metadata: {} },
];
const CITATIONS = [
  { id: 5, article_id: 31, block_id: 'b1', source_id: 10, quote: '带引用的段落', display_label: '行业报告', source_title: '行业报告', source_url: 'https://example.com/r', verification_status: 'supported', status: 'active', metadata: {} },
];

test('素材库：打开/搜索/插入正文', async ({ page }) => {
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => {
    if (r.request().method() === 'PUT') return r.fulfill({ json: { ok: true, version: 2 } });
    return r.fulfill({ json: ARTICLE });
  });
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/materials?*', r => r.fulfill({ json: { materials: MATERIALS } }));
  await page.route('**/api/materials/1', r => r.fulfill({ json: { material: MATERIALS[0] } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // 素材库打开
  await page.click('#btn-materials');
  await expect(page.locator('.mat-item')).toContainText('AI 市场规模数据');
  await expect(page.locator('.mat-item')).toContainText('数据'); // 标签
  // 搜索
  await page.fill('#mat-q', '市场规模');
  await expect(page.locator('.mat-item')).toContainText('AI 市场规模数据');
  // 插入正文
  await page.click('.mat-item [data-x="insert"]');
  const text = await page.evaluate(() => document.querySelector('#article .blk.edit').textContent);
  expect(text).toContain('2000 亿美元');
});

test('引用清单：核验状态/定位/移除', async ({ page }) => {
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/citations', r => r.fulfill({ json: { citations: CITATIONS } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/citations/5', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/citations/5/verification', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // 引用清单（面板头按钮）
  await page.click('#btn-cites');
  await expect(page.locator('.cite-row')).toContainText('行业报告');
  await expect(page.locator('.cite-row')).toContainText('支持'); // 核验状态中文
  // 定位正文（块存在 → 高亮）
  await page.click('.cite-row [data-x="locate"]');
  await expect(page.locator('#article .blk.edit.rv-flash')).toHaveCount(1);
});

test('Ask 回答卡：保存为素材/插入正文动作存在', async ({ page }) => {
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => {
    if (r.request().method() === 'PUT') return r.fulfill({ json: { ok: true, version: 2 } });
    return r.fulfill({ json: ARTICLE });
  });
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/ai/ask', r => r.fulfill({ json: { reply: '建议的标题：AI 时代创作者的真护城河', model: 'deepseek-x', ask_id: 9 } }));
  await page.route('**/api/projects/1/materials', r => r.fulfill({ json: { id: 99 } }));
  await page.route('**/api/asks/9/usage', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/asks', r => r.fulfill({ json: { asks: [{ id: 9, prompt: '取标题', response: '…', model: 'm', created_at: '2026-08-13T10:00:00', metadata: { usage: 'saved_as_material' } }] } }));
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  // Ask → 回答卡带固定动作名
  await page.fill('#ask-input', '给文章取个标题');
  await page.click('#ask-send');
  await expect(page.locator('#cardflow')).toContainText('保存为素材', { timeout: 5000 });
  await expect(page.locator('#cardflow')).toContainText('插入正文');
  // 问答历史入口
  await page.click('#ask-history-btn');
  await expect(page.locator('#cardflow')).toContainText('问答历史');
  await expect(page.locator('#cardflow')).toContainText('已存为素材');
});
