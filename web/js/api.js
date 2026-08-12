// API 客户端：fetch wrapper，错误规范化（支持 AbortSignal）

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(res.status + ' ' + t.slice(0, 100));
  }
  return res.json();
}
