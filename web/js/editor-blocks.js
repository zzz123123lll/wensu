// 块类型 ↔ DOM 渲染的单一映射（P1-1：Schema 声明的类型必须完整往返，不得降级 paragraph）。
// 渲染与收集共用本模块：collectBlocks 优先读 data-type，再按 tagName 兜底（兼容旧数据）。
// 依赖：security.js 的 escapeHtml / safeUrl

import { escapeHtml, safeUrl } from './security.js';

// tagName → block type（无 data-type 的旧数据兜底）
const TAG_TYPE = {
  H1: 'heading', H2: 'heading2', H3: 'heading3', H4: 'heading4',
  BLOCKQUOTE: 'blockquote', UL: 'unordered_list', OL: 'ordered_list',
  PRE: 'code', IMG: 'image', HR: 'divider', DIV: 'paragraph',
};

// 列表/代码块内部行收集（li 之间补 \n 还原 text）
function listText(el) {
  const lis = el.querySelectorAll('li');
  if (lis.length) return Array.from(lis).map(li => li.textContent).join('\n');
  return el.textContent; // 编辑中尚未生成 li 时兜底
}

// DOM 元素 → block dict（id/type/text/attrs）
export function collectBlockFromDom(el) {
  const type = (el.dataset && el.dataset.type) || TAG_TYPE[el.tagName] || 'paragraph';
  let text = '';
  let attrs = {};
  if (type === 'unordered_list' || type === 'ordered_list') {
    text = listText(el);
  } else if (type === 'image') {
    text = el.getAttribute('alt') || '';
    attrs = { url: el.getAttribute('src') || '' };
  } else {
    text = el.textContent;
  }
  return { id: el.dataset.bid, type, text, attrs };
}

// block dict → HTML（带 data-type，保证往返稳定）
export function blockHtml(b) {
  const bid = escapeHtml(b.id || '');
  const type = escapeHtml(b.type || 'paragraph');
  const cls = `blk edit${b.text ? '' : ' empty'}`;
  const common = `class="${cls}" contenteditable="true" data-bid="${bid}" data-type="${type}"`;
  switch (type) {
    case 'heading':
      return `<h1 ${common}>${escapeHtml(b.text)}</h1>`;
    case 'heading2':
      return `<h2 ${common}>${escapeHtml(b.text)}</h2>`;
    case 'heading3':
      return `<h3 ${common}>${escapeHtml(b.text)}</h3>`;
    case 'heading4':
      return `<h4 ${common}>${escapeHtml(b.text)}</h4>`;
    case 'blockquote':
      return `<blockquote ${common}>${escapeHtml(b.text)}</blockquote>`;
    case 'unordered_list':
      return `<ul ${common}>${b.text.split('\n').map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul>`;
    case 'ordered_list':
      return `<ol ${common}>${b.text.split('\n').map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ol>`;
    case 'code':
      return `<pre ${common}><code>${escapeHtml(b.text)}</code></pre>`;
    case 'image': {
      const url = safeUrl((b.attrs && b.attrs.url) || '');
      return `<img class="blk edit" contenteditable="false" data-bid="${bid}" data-type="image" src="${url}" alt="${escapeHtml(b.text)}">`;
    }
    case 'divider':
      return `<hr class="blk edit" contenteditable="false" data-bid="${bid}" data-type="divider">`;
    default:
      return `<div ${common}>${escapeHtml(b.text)}</div>`;
  }
}
