// review 启动器：Profile 选择（文章类型 / 发布目标 / 个人规则）+ 规则包版本概览

import { escapeHtml } from '../security.js';
import { reviewApi } from './api.js';

let packsCache = null;

export async function loadPacks() {
  if (packsCache) return packsCache;
  const r = await reviewApi('/api/review/packs');
  packsCache = r.packs;
  return r.packs;
}

export function packName(packs, pid) {
  const p = (packs || []).find(x => x.pack_id === pid);
  return p ? p.name : pid;
}

// 打开检查启动浮层（返回用户选择的 profile_selection；null = 取消）
export async function openLauncher() {
  const packs = await loadPacks();
  const builtin = packs.filter(p => p.builtin);
  const types = builtin.filter(p => ['opinion-essay', 'academic', 'work-report'].includes(p.pack_id));
  const channels = builtin.filter(p => ['wechat-mini', 'zhihu', 'toutiao', 'blog'].includes(p.pack_id));

  const wrap = document.createElement('div');
  wrap.className = 'review-launcher';
  wrap.innerHTML = `
    <div class="rl-head">成稿检查设置</div>
    <label>文章类型</label>
    <select id="rl-type">
      <option value="">通用（不选类型）</option>
      ${types.map(p => `<option value="${p.pack_id}">${escapeHtml(p.name)}</option>`).join('')}
    </select>
    <label>发布目标</label>
    <select id="rl-channel">
      <option value="">仅通用</option>
      ${channels.map(p => `<option value="${p.pack_id}">${escapeHtml(p.name)}</option>`).join('')}
    </select>
    <div class="rl-info">将运行规则包：
      <span id="rl-packs">通用基础 + 所选类型/渠道</span>
    </div>
    <div class="rl-acts">
      <button class="btn btn-g" id="rl-cancel">取消</button>
      <button class="btn btn-p" id="rl-run">开始检查</button>
    </div>`;
  document.body.appendChild(wrap);

  return new Promise(resolve => {
    const close = val => { wrap.remove(); resolve(val); };
    wrap.querySelector('#rl-cancel').onclick = () => close(null);
    const refresh = () => {
      const t = wrap.querySelector('#rl-type').value;
      const c = wrap.querySelector('#rl-channel').value;
      const names = ['common-markdown', t, c].filter(Boolean).map(pid => packName(packs, pid));
      wrap.querySelector('#rl-packs').textContent = names.join(' + ') || '通用基础';
    };
    wrap.querySelector('#rl-type').onchange = refresh;
    wrap.querySelector('#rl-channel').onchange = refresh;
    refresh();
    wrap.querySelector('#rl-run').onclick = () => close({
      common: ['common-markdown'],
      type: wrap.querySelector('#rl-type').value ? [wrap.querySelector('#rl-type').value] : [],
      channel: wrap.querySelector('#rl-channel').value ? [wrap.querySelector('#rl-channel').value] : [],
      personal: [],
    });
  });
}
