import { test, expect } from '@playwright/test';

// Phase 7：设置弹窗里的作者偏好（透明可删）+ 多模型配置（任务绑定）。

test('偏好添加/删除 + 模型添加/绑定', async ({ page }) => {
  const prefs = [];
  const profiles = [];
  const bindings = {};
  await page.route('**/api/projects', r => r.fulfill({ json: [] }));
  await page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  await page.route('**/api/prefs', async r => {
    if (r.request().method() === 'POST') {
      prefs.push(r.request().postDataJSON());
      return r.fulfill({ json: { ok: true } });
    }
    return r.fulfill({ json: { prefs } });
  });
  await page.route('**/api/prefs/*', async r => {
    prefs.length = 0;
    return r.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/profiles', async r => {
    if (r.request().method() === 'POST') {
      profiles.push({ id: profiles.length + 1, ...r.request().postDataJSON(), has_key: false });
      return r.fulfill({ json: { id: profiles.length } });
    }
    return r.fulfill({ json: { profiles, bindings } });
  });
  await page.route('**/api/bindings', async r => {
    const b = r.request().postDataJSON();
    bindings[b.task] = b.profile_id;
    return r.fulfill({ json: { ok: true } });
  });

  await page.goto('http://127.0.0.1:8790/');
  await page.click('#btn-settings');
  await page.waitForSelector('#settings-modal[style*="flex"]');

  // 添加偏好
  await page.fill('#pref-key', '文风');
  await page.fill('#pref-content', '多用短句');
  await page.click('#pref-add-btn');
  await page.waitForTimeout(400);
  await expect(page.locator('#prefs-list')).toContainText('文风');
  await expect(page.locator('#prefs-list')).toContainText('多用短句');

  // 删除偏好
  await page.click('#prefs-list .x');
  await page.waitForTimeout(400);
  await expect(page.locator('#prefs-list')).not.toContainText('文风');

  // 添加模型
  await page.fill('#pf-name', '写作模型');
  await page.fill('#pf-base', 'https://api.example.com/v1');
  await page.fill('#pf-model', 'model-x');
  await page.click('#pf-add-btn');
  await page.waitForTimeout(400);
  await expect(page.locator('#profiles-list')).toContainText('写作模型');

  // 绑定下拉出现并可绑定
  const sel = page.locator('#bind-row select[data-task="rewrite"]');
  await expect(sel).toHaveCount(1);
  await sel.selectOption('1');
  await page.waitForTimeout(400);
  expect(bindings.rewrite).toBe(1);
});
