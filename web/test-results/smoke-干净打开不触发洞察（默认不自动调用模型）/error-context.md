# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: smoke.spec.js >> 干净打开不触发洞察（默认不自动调用模型）
- Location: tests\e2e\smoke.spec.js:53:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: 0
Received: 1
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - generic [ref=e3]:
    - generic [ref=e4]: 文序
    - generic [ref=e5]: 第一篇
    - button "设置" [ref=e6] [cursor=pointer]
  - generic [ref=e10]:
    - generic [ref=e11]:
      - generic [ref=e12]:
        - button "＋ 新建项目" [ref=e13] [cursor=pointer]
        - button "收起/展开侧栏" [ref=e14] [cursor=pointer]
      - generic [ref=e17]:
        - generic [ref=e18] [cursor=pointer]:
          - generic [ref=e19]: ▶
          - generic [ref=e20]: 随笔
          - generic [ref=e21]: 1 篇
        - generic [ref=e22] [cursor=pointer]: ＋ 新建草稿
        - generic [ref=e23]: 第一篇
    - generic [ref=e30]:
      - generic [ref=e31]: 第一篇
      - generic [ref=e32]: 草稿 · 自动保存
      - generic [ref=e33]: 你好
    - generic [ref=e35]:
      - generic [ref=e36]:
        - generic [ref=e37]: 写作助手
        - generic [ref=e38]: mock
      - generic [ref=e41]:
        - generic [ref=e42]:
          - generic [ref=e43]: ◎
          - generic [ref=e44]: 当前洞察
          - generic [ref=e45]: AI 正在读
        - generic [ref=e46]:
          - generic [ref=e47]: 这段在说
          - generic [ref=e48]: s
        - generic [ref=e49]:
          - generic [ref=e50]: 缺什么
          - generic [ref=e51]: g
      - generic [ref=e52]:
        - textbox "问点什么…" [ref=e53]
        - button [ref=e54] [cursor=pointer]
      - generic [ref=e57]:
        - button "改写" [ref=e58] [cursor=pointer]
        - button "搜索" [ref=e62] [cursor=pointer]
        - button "核验" [ref=e67] [cursor=pointer]
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test';
  2  | 
  3  | // 冒烟：打开草稿 → 编辑 → 自动保存，保存请求携带正确 body。
  4  | // 所有 API 由浏览器端 route mock 提供（fake transport），不访问真实后端。
  5  | 
  6  | const MOCK = {
  7  |   projects: [{ id: 1, name: '随笔' }],
  8  |   articles: [{ id: 7, title: '第一篇', updated_at: '2026-01-01T00:00:00' }],
  9  |   article: {
  10 |     id: 7, project_id: 1, title: '第一篇', version: 1,
  11 |     blocks: [{ id: 'b1', type: 'paragraph', text: '你好', attrs: {} }],
  12 |     created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00',
  13 |   },
  14 |   settings: { configured: true, base_url: 'https://api.example.com/v1', model: 'mock', has_key: true },
  15 |   insight: { insight: { summary: 's', gap: 'g' }, suggestions: [] },
  16 | };
  17 | 
  18 | test('打开草稿、编辑并自动保存', async ({ page }) => {
  19 |   const savedBodies = [];
  20 | 
  21 |   await page.route('**/api/projects', r => r.fulfill({ json: MOCK.projects }));
  22 |   await page.route('**/api/projects/1/articles', r => r.fulfill({ json: MOCK.articles }));
  23 |   await page.route('**/api/articles/7', async r => {
  24 |     if (r.request().method() === 'PUT') {
  25 |       savedBodies.push(r.request().postDataJSON());
  26 |       return r.fulfill({ json: { ok: true, article_id: 7, version: 2, blocks_hash: 'h' } });
  27 |     }
  28 |     return r.fulfill({ json: MOCK.article });
  29 |   });
  30 |   await page.route('**/api/settings', r => r.fulfill({ json: MOCK.settings }));
  31 |   await page.route('**/api/ai/insight', r => r.fulfill({ json: MOCK.insight }));
  32 | 
  33 |   await page.goto('http://127.0.0.1:8790/');
  34 |   await expect(page.getByRole('heading', { name: '今天想写点什么？' })).toBeVisible();
  35 | 
  36 |   // 展开项目 → 打开草稿
  37 |   await page.click('.proj');
  38 |   await page.click('.doc[data-aid]');
  39 |   await expect(page.locator('#doc-title')).toHaveText('第一篇');
  40 | 
  41 |   // 编辑第一个 Block
  42 |   const block = page.locator('#article .blk.edit').first();
  43 |   await block.click();
  44 |   await page.keyboard.press('ControlOrMeta+a');
  45 |   await page.keyboard.type('你好世界，这是新内容。');
  46 | 
  47 |   // 等待自动保存（防抖 1.2s + 余量）
  48 |   await page.waitForTimeout(3000);
  49 |   expect(savedBodies.length).toBeGreaterThan(0);
  50 |   expect(savedBodies.at(-1).blocks[0].text).toContain('你好世界');
  51 | });
  52 | 
  53 | test('干净打开不触发洞察（默认不自动调用模型）', async ({ page }) => {
  54 |   let insightCalls = 0;
  55 |   await page.route('**/api/projects', r => r.fulfill({ json: MOCK.projects }));
  56 |   await page.route('**/api/projects/1/articles', r => r.fulfill({ json: MOCK.articles }));
  57 |   await page.route('**/api/articles/7', r => r.fulfill({ json: MOCK.article }));
  58 |   await page.route('**/api/settings', r => r.fulfill({ json: MOCK.settings }));
  59 |   await page.route('**/api/ai/insight', r => { insightCalls++; return r.fulfill({ json: MOCK.insight }); });
  60 | 
  61 |   await page.goto('http://127.0.0.1:8790/');
  62 |   await page.click('.proj');
  63 |   await page.click('.doc[data-aid]');
  64 |   await page.waitForTimeout(1000);
> 65 |   expect(insightCalls).toBe(0); // 打开草稿不自动调模型
     |                        ^ Error: expect(received).toBe(expected) // Object.is equality
  66 | });
  67 | 
```