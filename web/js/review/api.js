// review API：创建/读取/NDJSON 流/动作（不碰真实后端以外的服务）

export async function reviewApi(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(res.status + ' ' + t.slice(0, 120));
  }
  return res.json();
}

// 读取 NDJSON 流（stage/issue/warning/done），回调逐行处理
export async function readReviewStream(url, onEvent, signal) {
  const resp = await fetch(url, { signal });
  if (!resp.ok) throw new Error('流读取失败 ' + resp.status);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop() || '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try { onEvent(JSON.parse(line)); } catch { /* 坏行忽略 */ }
    }
  }
}
