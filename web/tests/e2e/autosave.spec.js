import { test, expect } from '@playwright/test';

// WEN-006 红测：autosave 状态机关键场景。
// 当前实现预期失败（红）——修复后转绿。

const BLOCKS = [
  { id: 'b1', type: 'paragraph', text: '第一段内容', attrs: {} },
  { id: 'b2', type: 'paragraph', text: '第二段内容', attrs: {} },
];

const ARTICLE_A = { id: 7, project_id: 1, title: '草稿A', version: 1, blocks: [BLOCKS[0]], created_at: '', updated_at: '' };
const ARTICLE_B = { id: 8, project_id: 1, title: '草稿B', version: 1, blocks: [BLOCKS[1]], created_at: '', updated_at: '' };

function setupRoutes(page, opts = {}) {
  const puts = [];
  const putsByAid = {};
  page.route('**/api/projects', r => r.fulfill({ json: [{ id: 1, name: '随笔' }] }));
  page.route('**/api/projects/1/articles', r => r.fulfill({ json: [{ id: 7, title: '草稿A', updated_at: '' }, { id: 8, title: '草稿B', updated_at: '' }] }));
  page.route('**/api/settings', r => r.fulfill({ json: { configured: true, base_url: 'https://x', model: 'm', has_key: true } }));
  page.route('**/api/ai/insight', r => r.fulfill({ json: { insight: { summary: 's', gap: 'g' }, suggestions: [] } }));
  page.route('**/api/articles/7', async r => {
    if (r.request().method() === 'PUT') {
      const body = r.request().postDataJSON();
      puts.push({ aid: 7, body });
      (putsByAid[7] = putsByAid[7] || []).push(body);
      await (opts.delayPut ? new Promise(res => setTimeout(res, opts.delayPut)) : Promise.resolve());
      if (opts.failVersion && body.base_version === opts.failVersion) {
        return r.fulfill({ status: 409, json: { detail: { code: 'version_conflict', current_version: 2, blocks: BLOCKS, blocks_hash: 'server' } } });
      }
      return r.fulfill({ json: { ok: true, article_id: 7, version: body.base_version + 1, blocks_hash: 'h' } });
    }
    return r.fulfill({ json: ARTICLE_A });
  });
  page.route('**/api/articles/8', async r => {
    if (r.request().method() === 'PUT') {
      const body = r.request().postDataJSON();
      puts.push({ aid: 8, body });
      (putsByAid[8] = putsByAid[8] || []).push(body);
      return r.fulfill({ json: { ok: true, article_id: 8, version: body.base_version + 1, blocks_hash: 'h' } });
    }
    return r.fulfill({ json: ARTICLE_B });
  });
  return { puts, putsByAid };
}

async function openArticle(page, aid) {
  await page.goto('http://127.0.0.1:8790/');
  await page.click('.proj');
  await page.click(`.doc[data-aid="${aid}"]`);
  await page.waitForSelector('#article .blk.edit');
}

test('RED 1: 编辑后 1 秒内切换草稿，旧稿内容不能写入新稿', async ({ page }) => {
  const { puts, putsByAid } = setupRoutes(page);
  await openArticle(page, 7);
  // 编辑旧稿
  const block = page.locator('#article .blk.edit').first();
  await block.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.type('旧稿新增内容');
  // 立即切换新稿（<1.2s 防抖窗口内）
  await page.click('.doc[data-aid="8"]');
  await page.waitForSelector('#article .blk.edit');
  await page.waitForTimeout(2500); // 等防抖保存触发
  // 关键断言：对草稿 8 的保存不能携带草稿 7 的内容
  for (const p of (putsByAid[8] || [])) {
    const text = p.body.blocks.map(b => b.text).join('');
    expect(text).not.toContain('旧稿新增内容');
  }
});

test('RED 2: Enter 新建段落后继续输入仍触发自动保存', async ({ page }) => {
  const { puts } = setupRoutes(page);
  await openArticle(page, 7);
  const block = page.locator('#article .blk.edit').first();
  await block.click();
  await page.keyboard.type('第一句');
  await expect(block).toHaveText(/第一句/); // 确认输入真的进入编辑器
  await expect(page.locator('#save-status')).toHaveText('未保存'); // markDirty 应已触发
  await page.keyboard.press('Enter');          // 拆出新块（事件委托）
  await page.keyboard.type('新块里的内容');       // 新块输入
  await page.waitForTimeout(4500);
  const last = puts.at(-1);
  expect(last).toBeTruthy();
  const texts = last.body.blocks.map(b => b.text).join('|');
  expect(texts).toContain('新块里的内容');
  expect(last.body.blocks.length).toBeGreaterThan(1);
});

test('RED 3: 保存进行中继续编辑，旧 ACK 不把新内容误标为已保存', async ({ page }) => {
  const { puts } = setupRoutes(page, { delayPut: 1500 }); // 保存响应慢，制造 in-flight
  await openArticle(page, 7);
  const block = page.locator('#article .blk.edit').first();
  await block.click();
  await page.keyboard.type('第一次编辑');
  await page.waitForTimeout(1400);  // 触发第一次保存（in-flight，1.5s 后才回）
  await page.keyboard.type('第二次编辑'); // in-flight 期间继续编辑
  await page.waitForTimeout(5000);  // 等第一次 ACK + 第二次保存
  const bodies = puts.map(p => p.body.blocks.map(b => b.text).join(''));
  const lastText = bodies.at(-1);
  expect(lastText).toContain('第二次编辑'); // 最终保存的是最新内容
});
