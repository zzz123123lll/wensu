// 安全工具：HTML 转义 + URL 白名单（纯函数，无 DOM 依赖）

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** 安全 URL：只允许 http/https，返回规范化 href；非法返回空字符串 */
export function safeUrl(u) {
  try {
    const p = new URL(u, window.location.origin);
    return (p.protocol === 'http:' || p.protocol === 'https:') ? p.href : '';
  } catch { return ''; }
}
