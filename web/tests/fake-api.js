// 可控 fake API transport：模拟后端行为，供单元测试使用。
// 能力：记录请求、按 URL 路由、延迟、失败注入（abort/network/bad-json）、
//       状态码（409/422/500）、请求体捕获。绝不访问真实模型或公网。
export class FakeApi {
  constructor() {
    this.requests = [];        // {url, method, body, seq}
    this.handlers = new Map(); // url -> async (req) => {status, body | raw}
    this.delays = new Map();   // url -> ms
    this.failures = new Map(); // url -> 'abort' | 'network' | 'bad-json'
    this.seq = 0;
    this.aborted = 0;
  }

  route(url, handler) { this.handlers.set(url, handler); }
  delay(url, ms) { this.delays.set(url, ms); }
  failWith(url, kind) { this.failures.set(url, kind); }

  /** 与浏览器 fetch 兼容的签名（支持 AbortSignal）。 */
  async fetch(url, opts = {}) {
    const seq = ++this.seq;
    let body = null;
    try { body = opts.body ? JSON.parse(opts.body) : null; } catch { body = opts.body; }
    this.requests.push({ url, method: opts.method || 'GET', body, seq });

    const delay = this.delays.get(url);
    if (delay) await new Promise(r => setTimeout(r, delay));

    const fail = this.failures.get(url);
    if (fail === 'abort') {
      // 永不返回，直到 Abort
      return new Promise((_, reject) => {
        opts.signal?.addEventListener('abort', () => {
          this.aborted++;
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        });
      });
    }
    if (fail === 'network') throw new TypeError('Failed to fetch');
    if (fail === 'bad-json') {
      return { ok: false, status: 500, async json() { throw new SyntaxError('Unexpected token'); }, async text() { return '<html>oops</html>'; } };
    }

    const handler = this.handlers.get(url);
    if (!handler) {
      return { ok: false, status: 404, async json() { return { detail: 'not found' }; }, async text() { return 'not found'; } };
    }
    const res = await handler({ body, seq });
    return {
      ok: res.status < 400,
      status: res.status,
      async json() { return res.body; },
      async text() { return JSON.stringify(res.body); },
    };
  }

  /** 请求次数（按 URL 前缀过滤可选） */
  count(urlPrefix = '') {
    return this.requests.filter(r => r.url.startsWith(urlPrefix)).length;
  }
}
