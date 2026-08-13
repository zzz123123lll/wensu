import { test, expect } from '@playwright/test';

// 方案 v1.0 Phase 2 E2E：启动检查 → issue 列表 → 定位 → 忽略 → 采用主稿修复 → 复检。

const ARTICLE = {
  id: 7, project_id: 1, title: '检查测试', version: 3,
  blocks: [
    { id: 'b1', type: 'heading', text: '标题', attrs: {} },
    { id: 'b2', type: 'heading2', text: '', attrs: {} },
    { id: 'b3', type: 'paragraph', text: '见 [危险](javascript:alert(1)) 链接', attrs: {} },
  ],
  created_at: '', updated_at: '',
};

const ISSUES = [
  { id: 1, review_id: 1, fingerprint: 'f1', rule_id: 'common.heading.empty', severity: 'warning',
    anchor: { block_id: 'b2', start_utf16: 0, end_utf16: 0, original_text: '' },
    suggestion: '', reason: '空标题：标题下没有文字内容', source_type: 'system', state: 'open' },
  { id: 2, review_id: 1, fingerprint: 'f2', rule_id: 'common.markdown.unsafe-url', severity: 'error',
    anchor: { block_id: 'b3', start_utf16: 3, end_utf16: 31, original_text: '[危险](javascript:alert(1))' },
    suggestion: '见 [危险](https://example.com) 链接', reason: '不安全链接协议：javascript://', source_type: 'system', state: 'open' },
  { id: 3, review_id: 1, fingerprint: 'f3', rule_id: 'common.language.repeated-word', severity: 'suggestion',
    anchor: { block_id: 'b3', start_utf16: 0, end_utf16: 4, original_text: '出现了了' },
    suggestion: '', reason: '疑似重复字', source_type: 'experience', state: 'open' },
];

const PROFILE = { rules: [
  { id: 'common.heading.empty', fix_mode: 'exact_patch' },
  { id: 'common.markdown.unsafe-url', fix_mode: 'exact_patch' },
  { id: 'common.language.repeated-word', fix_mode: 'advisory' },
] };

test('成稿检查：启动→列表→忽略→采用主稿修复→复检', async ({ page }) => {
  const accepted = [];
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '检查测试', updated_at: '' }] }));
  await page.route('**/api/articles/7', r => r.fulfill({ json: ARTICLE }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/7/citations', r => r.fulfill({ json: { citations: [] } }));
  await page.route('**/api/review/packs', r => r.fulfill({ json: { packs: [
    { pack_id: 'common-markdown', pack_version: '1.0.0', name: '通用基础规则', rule_count: 10, builtin: true },
    { pack_id: 'opinion-essay', pack_version: '1.0.0', name: '观点长文', rule_count: 4, builtin: true },
  ] } }));
  // 注意：更具体的 stream 路由必须注册在 /api/reviews 通配之前
  await page.route('**/api/reviews/1/stream', r => r.fulfill({
    status: 200, contentType: 'application/x-ndjson',
    body: ISSUES.map(i => JSON.stringify({ type: 'issue', issue: i })).join('\n') + '\n' + JSON.stringify({ type: 'done', status: 'completed' }) + '\n',
  }));
  await page.route('**/api/reviews/1/issues/*/ignore', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/reviews/1/issues/*/accept', async r => {
    const parts = new URL(r.request().url()).pathname.split('/');
    const iid = +parts[parts.length - 2];
    accepted.push(iid);
    return r.fulfill({ json: { action: 'master', new_version: 4, block_id: 'b3' } });
  });
  await page.route('**/api/reviews/1/recheck', r => r.fulfill({ json: { review_id: 2 } }));
  await page.route('**/api/reviews', r => r.fulfill({ json: { review_id: 1, issues: ISSUES, profile: PROFILE } }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid="7"]');
  await page.waitForSelector('#article .blk.edit');

  // 启动检查
  await page.click('#btn-review');
  await page.waitForSelector('.review-launcher');
  await expect(page.locator('.rl-info')).toContainText('通用基础规则');
  await page.click('#rl-run');
  await page.waitForSelector('.ai-card.review-panel .rv-item');
  await expect(page.locator('.rv-item')).toHaveCount(3);
  await expect(page.locator('.rv-item').first()).toContainText('空标题');

  // advisory 问题：显示"仅提示"，无"采用"按钮
  await expect(page.locator('.rv-item[data-iid="3"]')).toContainText('仅提示');
  await expect(page.locator('.rv-item[data-iid="3"] [data-x="accept"]')).toHaveCount(0);

  // 定位：点击 issue → 对应 block 高亮
  await page.click('.rv-item[data-iid="2"]');
  await expect(page.locator('#article .blk.edit[data-bid="b3"]')).toHaveClass(/rv-flash/);

  // 忽略第一条
  await page.click('.rv-item[data-iid="1"] [data-x="ignore"]');
  await page.waitForTimeout(300);
  await expect(page.locator('.rv-item')).toHaveCount(2);

  // 筛选按钮存在
  await expect(page.locator('.rv-filter')).toContainText('错误');

  // 导出双版本（mock：通用版/渠道版/摘要）——在采用主稿前（采用后刷新会重置面板）
  await page.route('**/api/reviews/1/exports', r => r.fulfill({ json: {
    export_id: 9,
    general_file: '检查测试-通用版.md',
    channel_file: '检查测试-wechatminiblog.md',
    stale: [],
    manifest: {},
  } }));
  await page.route('**/api/review-exports/9/general', r => r.fulfill({ body: '# 检查测试\n\n通用内容', contentType: 'text/markdown' }));
  await page.route('**/api/review-exports/9/channel', r => r.fulfill({ body: '# 检查测试\n\n渠道内容', contentType: 'text/markdown' }));
  await page.route('**/api/review-exports/9/report', r => r.fulfill({ json: { files: {}, issues: {} } }));
  await page.click('#rv-export');
  await page.waitForSelector('.ai-card.export-card');
  await expect(page.locator('.ai-card.export-card')).toContainText('通用版');
  // 切到渠道版 tab
  await page.click('.ex-tabs [data-tab="channel"]');
  await page.waitForTimeout(400);
  await expect(page.locator('#ex-body pre')).toContainText('渠道内容');

  // 采用第二条（主稿修复）
  await page.click('.rv-item[data-iid="2"] [data-x="accept"]');
  await page.waitForTimeout(400);
  expect(accepted).toEqual([2]);
});
