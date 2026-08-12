import { test, expect } from '@playwright/test';

// Phase 6：规则优先建议（无模型配置也可用）、手动标记、信号上报、限频关闭。

const ARTICLE = {
  id: 7, project_id: 1, title: '智能测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '需要帮助的段落内容', attrs: {} }],
  created_at: '', updated_at: '',
};

test('无模型配置时：手动标记 → 规则建议出现且可解释', async ({ page }) => {
  const signals = [];
  let suggestBody = null;
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '智能测试', updated_at: '' }] }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: false, base_url: '', model: '', has_key: false } }));
  await page.route('**/api/articles/7/citations', r => r.fulfill({ json: { citations: [] } }));
  await page.route('**/api/signals', async r => {
    const b = r.request().postDataJSON();
    signals.push(b);
    return r.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/copilot/suggest', async r => {
    suggestBody = r.request().postDataJSON();
    return r.fulfill({ json: {
      suggestions: [
        { id: 's1', type: 'rewrite', priority: 'high', title: '改写这段',
          description: '表达不顺时给几种说法', reason: '你标记了「表达不顺」',
          target_block_id: 'b1', actions: ['run', 'dismiss'], source: 'rule', confidence: 'high' },
      ],
      state: { stage: 'revising', issue: 'expression', focus: 'block' },
    } });
  });

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');

  // 打开草稿已上报 draft_open + 拉取建议
  await page.waitForTimeout(600);
  expect(signals.some(s => s.type === 'draft_open')).toBe(true);

  // 手动标记：hover 块 → ⋯ → 表达不顺
  const block = page.locator('#article .blk.edit').first();
  await block.hover();
  await page.waitForSelector('.block-menu');
  await page.click('.block-menu');
  await page.click('.mark-menu [data-i="expression"]');
  await page.waitForTimeout(600);
  expect(signals.some(s => s.type === 'mark' && s.issue === 'expression')).toBe(true);

  // 规则建议卡出现（含 reason）
  const card = page.locator('.ai-card.sug-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText('改写这段');
  await expect(card).toContainText('你标记了「表达不顺」'); // 可解释

  // 执行建议 → 无模型配置时提示配置（规则建议本身可用即完成门）
  await card.locator('[data-x="run"]').click();
  await expect(page.locator('#toast')).toContainText('配置', { timeout: 3000 });
});

test('关闭建议后同类不立即反复', async ({ page }) => {
  const signals = [];
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '智能测试', updated_at: '' }] }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: false, base_url: '', model: '', has_key: false } }));
  await page.route('**/api/articles/7/citations', r => r.fulfill({ json: { citations: [] } }));
  await page.route('**/api/signals', async r => {
    const b = r.request().postDataJSON();
    signals.push(b);
    return r.fulfill({ json: { ok: true } });
  });
  let sugs = [
    { id: 's1', type: 'search', priority: 'high', title: '查证资料', description: 'd',
      reason: '你标记了「需要资料」', target_block_id: 'b1', actions: ['run', 'dismiss'], source: 'rule', confidence: 'high' },
  ];
  await page.route('**/api/copilot/suggest', r => r.fulfill({ json: { suggestions: sugs, state: {} } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('.ai-card.sug-card');
  await page.click('.ai-card.sug-card [data-x="dismiss"]');
  await page.waitForTimeout(400);
  // 关闭后建议卡消失，且上报了 dismiss
  await expect(page.locator('.ai-card.sug-card')).toHaveCount(0);
  expect(signals.some(s => s.type === 'dismiss')).toBe(true);
});
