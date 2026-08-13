import { test, expect } from '@playwright/test';

// P1-⑦ 图片上传：dock「图片」→ 选择文件 → 上传 → 插入 image Block → 保存。

const ARTICLE = {
  id: 34, project_id: 1, title: '图片上传测试', version: 1,
  blocks: [{ id: 'b1', type: 'paragraph', text: '文字段。' }],
};

const PNG = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0]);

test('图片上传并插入 image Block', async ({ page }) => {
  let saved = null;
  await page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  await page.route('**/api/projects/*/articles', r => r.fulfill({ json: [{ id: ARTICLE.id, title: ARTICLE.title }] }));
  await page.route('**/api/articles/' + ARTICLE.id, r => {
    if (r.request().method() === 'PUT') {
      saved = r.request().postDataJSON();
      return r.fulfill({ json: { ok: true, version: 2 } });
    }
    return r.fulfill({ json: ARTICLE });
  });
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/articles/' + ARTICLE.id + '/continue', r => r.fulfill({ json: { last_edited: '', recent_materials: [], pending_review: 0 } }));
  await page.route('**/api/signals', r => r.fulfill({ json: { ok: true } }));
  await page.route('**/api/uploads/image', r => r.fulfill({ json: { url: '/uploads/abc.png', bytes: PNG.length } }));
  await page.route('**/uploads/abc.png', r => r.fulfill({ body: PNG, contentType: 'image/png' }));

  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click('.doc[data-aid]');
  await page.waitForSelector('#article .blk.edit');

  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.click('#tool-img'),
  ]);
  await chooser.setFiles({ name: 'photo.png', mimeType: 'image/png', buffer: PNG });

  // image Block 出现（data-type=image，src 为上传地址）
  const img = page.locator('#article img[data-type="image"]');
  await expect(img).toBeVisible({ timeout: 5000 });
  await expect(img).toHaveAttribute('src', '/uploads/abc.png');
  // 保存请求包含 image 块（blocks 往返不降级）
  await expect.poll(() => saved, { timeout: 8000 }).not.toBeNull();
  expect(saved.blocks.some(b => b.type === 'image' && b.attrs.url === '/uploads/abc.png')).toBe(true);
});
