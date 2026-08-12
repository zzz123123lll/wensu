import { describe, expect, it } from 'vitest';
import { FakeApi } from '../fake-api.js';

describe('FakeApi transport', () => {
  it('记录请求方法与请求体', async () => {
    const api = new FakeApi();
    api.route('/api/articles/1', () => ({ status: 200, body: { ok: true } }));
    await api.fetch('/api/articles/1', { method: 'PUT', body: JSON.stringify({ blocks: [{ id: 'b1' }] }) });
    expect(api.requests).toHaveLength(1);
    expect(api.requests[0].method).toBe('PUT');
    expect(api.requests[0].body.blocks[0].id).toBe('b1');
  });

  it('未路由的 URL 返回 404', async () => {
    const api = new FakeApi();
    const res = await api.fetch('/api/unknown');
    expect(res.status).toBe(404);
  });

  it('409/422/500 状态透传', async () => {
    const api = new FakeApi();
    api.route('/api/conflict', () => ({ status: 409, body: { code: 'version_conflict' } }));
    api.route('/api/validate', () => ({ status: 422, body: { detail: 'missing base_version' } }));
    api.route('/api/boom', () => ({ status: 500, body: { detail: 'x' } }));
    expect((await api.fetch('/api/conflict')).status).toBe(409);
    expect((await api.fetch('/api/validate')).status).toBe(422);
    expect((await api.fetch('/api/boom')).status).toBe(500);
  });

  it('bad-json：json() 抛 SyntaxError', async () => {
    const api = new FakeApi();
    api.failWith('/api/bad', 'bad-json');
    const res = await api.fetch('/api/bad');
    await expect(res.json()).rejects.toThrow(SyntaxError);
  });

  it('abort：永不返回，Abort 后以 AbortError 拒绝', async () => {
    const api = new FakeApi();
    api.failWith('/api/slow', 'abort');
    const ctrl = new AbortController();
    const p = api.fetch('/api/slow', { signal: ctrl.signal });
    let settled = false;
    p.catch(() => { settled = true; });
    await new Promise(r => setTimeout(r, 30));
    expect(settled).toBe(false); // 未 abort 前不返回
    ctrl.abort();
    await expect(p).rejects.toThrow('aborted');
    expect(api.aborted).toBe(1);
  });

  it('延迟：慢响应晚于快响应返回（乱序场景）', async () => {
    const api = new FakeApi();
    const order = [];
    api.route('/api/fast', async () => { order.push('fast'); return { status: 200, body: {} }; });
    api.route('/api/slow', async () => { order.push('slow'); return { status: 200, body: {} }; });
    api.delay('/api/slow', 50);
    const slowP = api.fetch('/api/slow');
    const fastP = api.fetch('/api/fast');
    await fastP;
    expect(order).toEqual(['fast']);
    await slowP;
    expect(order).toEqual(['fast', 'slow']);
  });

  it('network 失败抛 TypeError', async () => {
    const api = new FakeApi();
    api.failWith('/api/net', 'network');
    await expect(api.fetch('/api/net')).rejects.toThrow(TypeError);
  });
});
