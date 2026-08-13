/* 文序 · 前端 bootstrap（UI 逻辑；工具与状态机在 js/ 模块） */
'use strict';

import { api } from './api.js';
import { $, toast_, sstate, cancelPendingSave, markDirty, saveNow, collectBlocks, pushUndo, popUndo, renderBlocks } from './state.js';
import { escapeHtml, safeUrl } from './security.js';

// 供 review 等动态加载模块引用（ES module 顶层声明是模块私有，须显式导出）
export { toast_, openArticle };
export let currentAid = null;      // 当前打开草稿 id

let projects = [];          // [{id, name}]
let expanded = {};          // pid -> bool
let currentPid = null;
let articleReqSeq = 0;  // 打开文章请求序号：迟到的响应不得覆盖新稿
let insightAbort = null; // 洞察请求取消句柄

/* ---------- 左栏 ---------- */
async function loadProjects() {
  projects = await api('/api/projects');
  renderSide();
}

function renderSide() {
  $('#side-list').innerHTML = projects.map(p => {
    const items = []; // 草稿在打开项目时异步加载
    const open = !!expanded[p.id];
    return `
      <div class="proj ${open ? 'open' : ''}" data-pid="${p.id}">
        <span class="arr">▶</span><span>${escapeHtml(p.name)}</span><span class="cnt" data-cnt="${p.id}"></span>
      </div>
      <div class="doc sub newdraft" data-pid="${p.id}" data-new="1" style="display:${open ? '' : 'none'}">＋ 新建草稿</div>
      <div class="docs-${p.id}" style="display:${open ? '' : 'none'}"></div>
    `;
  }).join('');

  $('#side-list').querySelectorAll('.proj').forEach(proj => {
    proj.setAttribute('role', 'button');
    proj.tabIndex = 0;
    proj.addEventListener('click', async () => {
      const pid = +proj.dataset.pid;
      expanded[pid] = !expanded[pid];
      renderSide();
      if (expanded[pid]) await loadArticles(pid);
    });
    proj.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); proj.click(); }
    });
  });
  $('#side-list').querySelectorAll('.doc[data-new]').forEach(row => row.addEventListener('click', e => {
    e.stopPropagation();
    inlineName('草稿标题', async name => {
      const pid = +row.dataset.pid;
      const created = await api(`/api/projects/${pid}/articles`, { method: 'POST', body: JSON.stringify({ title: name }) });
      await loadArticles(pid);
      openArticle(created.id); // 新建即打开
      toast_('已新建草稿「' + name + '」');
    });
  }));
}

async function loadArticles(pid) {
  const arts = await api(`/api/projects/${pid}/articles`);
  const box = document.querySelector(`.docs-${pid}`);
  const cnt = document.querySelector(`[data-cnt="${pid}"]`);
  if (cnt) cnt.textContent = arts.length + ' 篇';
  if (!box) return;
  box.innerHTML = arts.map(a => `
    <div class="doc sub ${currentAid === a.id ? 'active' : ''}" data-aid="${a.id}">
      <span class="dot"></span><span class="name">${escapeHtml(a.title)}</span>
      <span class="del" data-del="${a.id}" title="移入回收站" aria-label="删除草稿">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2 4h12M6 4V2.5h4V4M4 4l.7 9h6.6l.7-9M6.5 7v3.5M9.5 7v3.5"/></svg>
      </span>
    </div>`).join('');
  box.querySelectorAll('.doc[data-aid]').forEach(d => {
    d.setAttribute('role', 'button');
    d.tabIndex = 0;
    d.addEventListener('click', () => openArticle(+d.dataset.aid));
    d.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); d.click(); }
    });
  });
  box.querySelectorAll('.del').forEach(del => del.addEventListener('click', async e => {
    e.stopPropagation();
    const aid = +del.dataset.del;
    if (!confirm('把这篇草稿移入回收站？（可恢复）')) return;
    await api(`/api/articles/${aid}`, { method: 'DELETE' });
    if (currentAid === aid) { currentAid = null; location.reload(); }
    await loadArticles(pid);
    toast_('已移入回收站');
  }));
}

/* ---------- 命名浮层 ---------- */
function inlineName(placeholder, cb) {
  const wrap = document.createElement('div');
  wrap.className = 'name-input';
  wrap.innerHTML = `<input placeholder="${placeholder}" maxlength="30"><div class="ni-acts"><button class="ni-ok">确定</button></div>`;
  document.body.appendChild(wrap);
  const inp = wrap.querySelector('input');
  inp.focus();
  const done = () => { const v = inp.value.trim(); wrap.remove(); if (v) cb(v); };
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') done(); if (e.key === 'Escape') wrap.remove(); });
  wrap.querySelector('.ni-ok').addEventListener('click', done);
}

/* ---------- 中间区：Block 编辑器 ---------- */
function openArticle(aid) {
  const seq = ++articleReqSeq;
  if (drawerOpen === 'proj') closeDrawer(); // 选择草稿后自动关项目抽屉
  // 切稿前 flush 旧稿：清 timer + 立即保存旧稿（per-aid 状态，不会写错稿）
  if (currentAid && currentAid !== aid) {
    cancelPendingSave();
    const old = sstate(currentAid);
    if (old.dirty) saveNow(currentAid);
  }
  currentAid = aid;
  api(`/api/articles/${aid}`).then(a => {
    if (seq !== articleReqSeq) return; // 迟到的旧稿响应丢弃
    currentPid = a.project_id;
    sstate(aid).baseVersion = a.version || 1;
    $('#empty').style.display = 'none';
    const art = $('#article');
    art.classList.add('show');
    $('#doc-title').textContent = a.title;

    let html = `<div class="art-title">${escapeHtml(a.title)}</div>`;
    html += `<div class="art-meta">草稿 · <span id="save-status" class="save-status"></span>
      <span class="meta-ops"><button class="mini2" id="btn-history">历史</button><button class="mini2" id="btn-review">成稿检查</button><button class="mini2" id="btn-export">导出</button></span></div>`;
    if (a.blocks.length === 0) {
      html += `<div class="blk edit empty" contenteditable="true" data-bid="${crypto.randomUUID()}"></div>`;
    } else {
      html += a.blocks.map(b => blockHtml(b)).join('');
    }
    art.innerHTML = html;
    bindEditor();
    renderCitationBadges(); // 引用编号由服务端数据计算，badge 不写入正文 text
    const bh = $('#btn-history');
    if (bh) bh.addEventListener('click', showHistory);
    const br = $('#btn-review');
    if (br) br.addEventListener('click', () => { import('/js/review/panel.js').then(m => m.runReview(aid)).catch(e => toast_('检查模块加载失败：' + e.message)); });
    const be = $('#btn-export');
    if (be) be.addEventListener('click', () => { location.href = `/api/articles/${aid}/export`; });
    // 高亮左栏当前草稿
    document.querySelectorAll('.doc[data-aid]').forEach(d => d.classList.toggle('active', +d.dataset.aid === aid));
    $('#doc-scroll').scrollTop = 0;
    showInsightIdle(); // 默认不自动调用模型（WEN-009 安全默认）
    reportSignal('draft_open', { blocks_count: a.blocks.length }); // Phase 6 信号
    loadSuggestions(); // 规则建议（无模型也可用）
  }).catch(e => toast_('打开失败：' + e.message));
}

function blockHtml(b) {
  if (b.type === 'heading') {
    return `<h2 class="blk edit" contenteditable="true" data-bid="${b.id}">${escapeHtml(b.text)}</h2>`;
  }
  if (b.type === 'blockquote') {
    return `<blockquote class="blk edit" contenteditable="true" data-bid="${b.id}">${escapeHtml(b.text)}</blockquote>`;
  }
  return `<div class="blk edit ${b.text ? '' : 'empty'}" contenteditable="true" data-bid="${b.id}">${escapeHtml(b.text)}</div>`;
}

/* ---------- 事件委托（Enter 新建块后输入仍触发保存） ---------- */
let composing = false; // 中文 IME 组合中

function bindEditor() {
  const art = $('#article');
  art.addEventListener('compositionstart', () => { composing = true; });
  art.addEventListener('compositionend', e => {
    composing = false;
    const block = e.target.closest ? e.target.closest('.blk.edit') : null;
    if (block) {
      block.classList.toggle('empty', block.textContent === '');
      markDirty(currentAid); // 组合结束后生成完整快照
    }
  });
  art.addEventListener('paste', e => {
    // 最小安全粘贴：只取纯文本，剔除 HTML/脚本/事件属性
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData('text/plain') || '';
    if (text) {
      document.execCommand('insertText', false, text);
      markDirty(currentAid);
    }
  });
  art.addEventListener('keydown', e => {
    const block = e.target.closest ? e.target.closest('.blk.edit') : null;
    if (!block) return;
    if (e.key === 'Enter' && !e.shiftKey && !composing && !e.isComposing) {
      e.preventDefault();
      const next = document.createElement('div');
      next.className = 'blk edit empty';
      next.contentEditable = 'true';
      next.dataset.bid = crypto.randomUUID(); // 创建即稳定 UUID
      block.after(next);
      next.focus();
      markDirty(currentAid);
    } else if (e.key === 'Backspace' && block.textContent === '' && !e.shiftKey) {
      const prev = block.previousElementSibling;
      if (prev && prev.classList.contains('blk.edit')) {
        e.preventDefault();
        block.remove();
        prev.focus();
        markDirty(currentAid);
      }
    }
  });
  art.addEventListener('input', e => {
    const block = e.target.closest ? e.target.closest('.blk.edit') : null;
    if (block) {
      block.classList.toggle('empty', block.textContent === '');
      markDirty(currentAid);
    }
  });
  // 块标记入口：hover 显示 ⋯，点击弹问题菜单（Phase 6 显式信号）
  art.addEventListener('mouseover', e => {
    const block = e.target.closest ? e.target.closest('.blk.edit') : null;
    if (block && !block.querySelector('.block-menu') && block.textContent.trim()) {
      const m = document.createElement('span');
      m.className = 'block-menu';
      m.textContent = '⋯';
      m.title = '标记这段的问题';
      m.setAttribute('aria-label', '标记问题');
      block.appendChild(m);
    }
  });
  art.addEventListener('mouseleave', () => {
    document.querySelectorAll('#article .block-menu').forEach(m => m.remove());
  });
  art.addEventListener('click', e => {
    const btn = e.target.closest ? e.target.closest('.block-menu') : null;
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      const block = btn.closest('.blk.edit');
      showMarkMenu(btn, block);
    }
  });
}

/* ---------- 新建项目 ---------- */
$('#btn-new-proj').addEventListener('click', () => {
  inlineName('项目名称', async name => {
    await api('/api/projects', { method: 'POST', body: JSON.stringify({ name }) });
    await loadProjects();
    toast_('已创建项目「' + name + '」');
  });
});

$('#btn-fold').addEventListener('click', () => $('#sidebar').classList.toggle('collapsed'));

/* 全局撤销（焦点可在正文外）：AI 应用/版本恢复前已入栈（WEN-023） */
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey && !composing && !e.isComposing) {
    e.preventDefault();
    const prev = popUndo(currentAid);
    if (prev) {
      renderBlocks(prev);
      renderCitationBadges();
      markDirty(currentAid);
      saveNow(currentAid);
      toast_('已撤销');
    } else {
      toast_('没有可撤销的操作');
    }
  }
});

/* ---------- Phase 7：作者偏好 + 多模型配置（设置弹窗内） ---------- */
const TASKS = [['ask', 'Ask'], ['rewrite', '改写'], ['insight', '洞察'], ['search_synthesis', '搜索'], ['check', '核验']];

async function loadPrefs() {
  try {
    const r = await api('/api/prefs');
    const box = $('#prefs-list');
    box.innerHTML = r.prefs.length ? r.prefs.map(p =>
      `<div class="pf-item"><span class="k">${escapeHtml(p.key)}</span><span>${escapeHtml(p.content)}</span><span class="x" data-del="${escapeHtml(p.key)}" title="删除">✕</span></div>`
    ).join('') : '<div style="font-size:12px;color:var(--fg-3)">还没有偏好。Ask 回答里可点「记住」保存。</div>';
    box.querySelectorAll('.x').forEach(x => x.onclick = async () => {
      await api('/api/prefs/' + encodeURIComponent(x.dataset.del), { method: 'DELETE' });
      loadPrefs();
      toast_('已删除偏好');
    });
  } catch { /* 偏好加载失败不打扰 */ }
}

async function loadProfiles() {
  try {
    const r = await api('/api/profiles');
    const box = $('#profiles-list');
    box.innerHTML = r.profiles.length ? r.profiles.map(p =>
      `<div class="pf-item"><span class="k">${escapeHtml(p.name)}</span><span class="m">${escapeHtml(p.model)} · ${p.has_key ? '已配 Key' : '无 Key'}</span>
       <button class="mini2" data-test="${p.id}">测试</button>
       <span class="x" data-del="${p.id}" title="删除">✕</span></div>`
    ).join('') : '<div style="font-size:12px;color:var(--fg-3)">还没有额外模型。上方「默认模型」即主配置。</div>';
    box.querySelectorAll('[data-test]').forEach(b => b.onclick = async () => {
      try {
        const t = await api(`/api/profiles/${b.dataset.test}/test`, { method: 'POST' });
        toast_('连接正常：' + t.model);
      } catch (e) { toast_('连接失败：' + e.message); }
    });
    box.querySelectorAll('.x[data-del]').forEach(x => x.onclick = async () => {
      await api('/api/profiles/' + x.dataset.del, { method: 'DELETE' });
      loadProfiles();
      toast_('已删除模型');
    });
    // 任务绑定下拉
    const br = $('#bind-row');
    br.innerHTML = '任务绑定：' + TASKS.map(([task, label]) => {
      const cur = r.bindings[task];
      return `<span>${label} <select data-task="${task}">
        <option value="">默认模型</option>
        ${r.profiles.map(p => `<option value="${p.id}" ${cur === p.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('')}
      </select></span>`;
    }).join('');
    br.querySelectorAll('select').forEach(sel => sel.onchange = async () => {
      await api('/api/bindings', { method: 'PUT', body: JSON.stringify({ task: sel.dataset.task, profile_id: +sel.value }) });
      toast_('已绑定');
    });
  } catch { /* 不打扰 */ }
}

/* 设置弹窗打开时加载偏好与模型配置 */
const _origOpen = $('#btn-settings').onclick;
$('#btn-settings').addEventListener('click', () => { loadPrefs(); loadProfiles(); });
import('/js/review/rules.js').then(m => {
  m.loadRulesSection();
  const btn = $('#rules-import-btn');
  if (btn) btn.addEventListener('click', () => m.openRuleImport());
}).catch(() => {});

$('#pref-add-btn').addEventListener('click', async () => {
  const key = $('#pref-key').value.trim();
  const content = $('#pref-content').value.trim();
  if (!key || !content) { toast_('偏好名和内容都要填'); return; }
  await api('/api/prefs', { method: 'POST', body: JSON.stringify({ key, content }) });
  $('#pref-key').value = ''; $('#pref-content').value = '';
  loadPrefs();
  toast_('已记住偏好，Ask 时会自动参考');
});

$('#pf-add-btn').addEventListener('click', async () => {
  const name = $('#pf-name').value.trim();
  const base_url = $('#pf-base').value.trim();
  const model = $('#pf-model').value.trim();
  const api_key = $('#pf-key').value.trim();
  if (!name || !base_url || !model) { toast_('名称/地址/模型都要填'); return; }
  try {
    await api('/api/profiles', { method: 'POST', body: JSON.stringify({ name, base_url, model, api_key: api_key || null }) });
    $('#pf-name').value = ''; $('#pf-base').value = ''; $('#pf-model').value = ''; $('#pf-key').value = '';
    loadProfiles();
    toast_('已添加模型，可在任务绑定里选用');
  } catch (e) { toast_('添加失败：' + e.message); }
});
let cfg = { configured: false, base_url: '', model: '' };

async function loadSettings() {
  try { cfg = await api('/api/settings'); } catch (e) { /* 忽略 */ }
  updatePanelStatus();
}

function updatePanelStatus() {
  const st = document.querySelector('.pstatus');
  if (!st) return;
  st.innerHTML = cfg.configured
    ? `<span class="pdot" style="background:#34c759"></span>${escapeHtml(cfg.model)}`
    : `<span class="pdot" style="background:#ff9f0a"></span>未配置模型`;
}

const PRESETS = {
  deepseek: { name: 'DeepSeek', base: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash' },
  openai: { name: 'OpenAI', base: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  qwen: { name: '通义千问', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  kimi: { name: 'Kimi', base: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  zhipu: { name: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  custom: { name: '自定义', base: '', model: '' },
};

function openSettings() {
  $('#set-base').value = cfg.base_url || '';
  $('#set-model').value = cfg.model || '';
  $('#set-key').value = '';
  // 高亮匹配的预设（按已存地址反推）
  const saved = (cfg.base_url || '').toLowerCase();
  let matched = 'custom';
  for (const [k, v] of Object.entries(PRESETS)) {
    if (v.base && saved.startsWith(v.base.toLowerCase().split('/v1')[0])) { matched = k; break; }
  }
  document.querySelectorAll('.preset').forEach(b => b.classList.toggle('on', b.dataset.p === matched));
  $('#settings-modal').style.display = 'flex';
}

document.querySelectorAll('.preset').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.preset').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  const p = PRESETS[btn.dataset.p];
  if (p) { $('#set-base').value = p.base; $('#set-model').value = p.model; }
}));
function closeSettings() { $('#settings-modal').style.display = 'none'; }

$('#btn-settings').addEventListener('click', openSettings);
$('#modal-close').addEventListener('click', closeSettings);
$('#modal-cancel').addEventListener('click', closeSettings);
$('#settings-modal').addEventListener('click', e => { if (e.target.id === 'settings-modal') closeSettings(); });
$('#modal-save').addEventListener('click', async () => {
  const body = { base_url: $('#set-base').value.trim(), model: $('#set-model').value.trim() };
  const key = $('#set-key').value.trim();
  if (key) body.api_key = key;
  if (!body.base_url || !body.model) { toast_('请填写 API 地址和模型名'); return; }
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
    cfg = await api('/api/settings');
    updatePanelStatus();
    closeSettings();
    toast_(cfg.configured ? '模型配置已保存，可直接使用' : '保存成功，但还缺 API Key');
  } catch (e) { toast_('保存失败：' + e.message); }
});

function requireCfg() {
  if (cfg.configured) return true;
  toast_('请先在设置里配置模型（API Key + 模型名）');
  openSettings();
  return false;
}

/* ========== 右栏卡片（通用） ========== */
function addPanelCard(title, body, opts = {}) {
  const card = document.createElement('div');
  card.className = 'insight' + (opts.user ? ' userreq' : '');
  card.innerHTML = `
    <div class="ins-head">
      <span class="ins-ic">${opts.user ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><path d="M6 6.5a2 2 0 1 1 2.8 1.8c-.5.3-.8.6-.8 1.2M8 12h.01"/></svg>' : '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l1.6 4.2L14 6.6l-3.6 2.6L11.4 14 8 11.2 4.6 14l1-4.8L2 6.6l4.4-1.4L8 1z"/></svg>'}</span>
      <span class="ins-t">${escapeHtml(title)}</span>
    </div>
    <div class="ins-row"><span class="v" style="width:auto;white-space:pre-wrap">${escapeHtml(body)}</span></div>`;
  $('#cardflow').appendChild(card);
  $('#cardflow').scrollTop = $('#cardflow').scrollHeight;
  return card;
}

/* ========== Ask 链路 ========== */
$('#ask-send').addEventListener('click', sendAsk);
$('#ask-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendAsk(); });

async function sendAsk() {
  const input = $('#ask-input');
  const t = input.value.trim();
  if (!t) return;
  if (!requireCfg()) return;
  addPanelCard('你问', t, { user: true });
  input.value = '';
  const busy = addPanelCard('思考中', '…');
  try {
    const ctx = collectBlocks().map(b => b.text).filter(Boolean).join('\n').slice(0, 3000);
    const r = await api('/api/ai/ask', { method: 'POST', body: JSON.stringify({ prompt: t, context: ctx, article_id: currentAid }) });
    busy.remove();
    // 回答卡：显示实际模型 + 「记住偏好」入口（作者记忆，透明可删）
    const card = addPanelCard('回答' + (r.model ? ' · ' + escapeHtml(r.model) : ''), r.reply, { model: r.model });
    const remember = document.createElement('button');
    remember.className = 'mini2';
    remember.textContent = '记住偏好';
    remember.title = '把这条回答里的写作偏好存进记忆（设置中可删）';
    remember.style.marginTop = '6px';
    remember.onclick = () => {
      const key = prompt('偏好名（如：文风）');
      if (!key) return;
      const content = prompt('偏好的内容（如：多用短句）');
      if (!content) return;
      api('/api/prefs', { method: 'POST', body: JSON.stringify({ key, content }) }).then(() => toast_('已记住，Ask 时会自动参考')).catch(e => toast_('保存失败：' + e.message));
    };
    card.appendChild(remember);
  } catch (e) {
    busy.remove();
    addPanelCard('出错', e.message);
  }
}

/* ========== 改写链路 ========== */
function anchorFromSel() {
  const s = window.getSelection();
  if (!s.rangeCount) return null;
  let n = s.getRangeAt(0).startContainer;
  while (n && !(n.classList && n.classList.contains('blk'))) n = n.parentElement;
  return n && n.classList && n.classList.contains('blk') ? n : null;
}
function firstBlock() {
  return document.querySelector('#article .blk.edit');
}

/* ---------- 写作智能信号上报（Phase 6：显式信号，不落正文） ---------- */
function reportSignal(type, extra = {}) {
  if (!currentAid) return;
  fetch('/api/signals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ article_id: currentAid, type, ...extra }),
  }).catch(() => {});
}

/* 规则建议：打开草稿/手动标记后拉取（无模型配置也可用） */
async function loadSuggestions() {
  if (!currentAid) return;
  try {
    const r = await api('/api/copilot/suggest', { method: 'POST', body: JSON.stringify({ article_id: currentAid }) });
    renderCopilotSuggestions(r.suggestions || []);
  } catch { /* 建议失败不打扰 */ }
}

function renderCopilotSuggestions(sugs) {
  document.querySelectorAll('.ai-card.sug-card').forEach(c => c.remove());
  if (!sugs.length) return;
  const card = document.createElement('div');
  card.className = 'ai-card sug-card';
  const iconFor = a => a === 'search'
    ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg>'
    : a === 'check'
      ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 4.5L6.5 11 3 7.5"/><circle cx="8" cy="8" r="6.5"/></svg>'
      : a === 'rewrite'
        ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2l3 3L5.5 13.5 2 14l.5-3.5L11 2z"/></svg>'
        : a === 'structure'
          ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="2" y="2" width="12" height="5" rx="1"/><rect x="2" y="9" width="12" height="5" rx="1"/></svg>'
          : '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8 2v12M8 2l-3 3M8 2l3 3M8 14l-3-3M8 14l3-3"/></svg>';
  card.innerHTML = '<div class="ai-head">写作助手建议 <span class="cnt">' + sugs.length + '</span></div>' +
    sugs.map(s => `<div class="sug-item" data-t="${s.type}" data-bid="${escapeHtml(s.target_block_id || '')}">
      <span class="s-ic">${iconFor(s.type)}</span>
      <div class="s-main"><div class="t">${escapeHtml(s.title)}</div>
      <div class="d">${escapeHtml(s.description)}</div>
      <div class="r">${escapeHtml(s.reason)}</div>
      <div class="res-acts"><button class="mini2" data-x="run">执行</button><button class="mini2" data-x="dismiss">关闭</button></div>
      </div></div>`).join('');
  const flow = $('#cardflow');
  flow.prepend(card);
  card.querySelectorAll('[data-x="run"]').forEach((b, i) => b.onclick = () => runSuggestion(sugs[i]));
  card.querySelectorAll('[data-x="dismiss"]').forEach((b, i) => b.onclick = async () => {
    const s = sugs[i];
    await api('/api/signals', { method: 'POST', body: JSON.stringify({ article_id: currentAid, type: 'dismiss' }) }).catch(() => {});
    card.remove();
    toast_('已关闭这类建议');
  });
}

function runSuggestion(s) {
  const block = s.target_block_id ? document.querySelector(`#article .blk.edit[data-bid="${s.target_block_id}"]`) : firstBlock();
  if (s.type === 'rewrite') runRewrite(block || firstBlock());
  else if (s.type === 'search') runSearch(block || firstBlock());
  else if (s.type === 'check') runCheck(block || firstBlock());
  else if (s.type === 'structure') loadInsight(currentAid);
  else if (s.type === 'ask') { $('#ask-input').focus(); }
}

/* 块标记菜单：表达不顺 / 需要资料 / 需要结构 / 需要观点 */
function showMarkMenu(btn, block) {
  const menu = document.createElement('div');
  menu.className = 'mark-menu';
  menu.innerHTML = '<button data-i="expression">表达不顺</button><button data-i="facts">需要资料</button><button data-i="structure">需要结构</button><button data-i="ideas">需要观点</button>';
  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  menu.style.left = Math.min(r.left, window.innerWidth - 150) + 'px';
  menu.style.top = (r.bottom + 4) + 'px';
  const close = () => menu.remove();
  menu.querySelectorAll('button').forEach(b => b.onclick = () => {
    const issue = b.dataset.i;
    reportSignal('mark', { issue, focus: 'block', block_id: block.dataset.bid });
    loadSuggestions();
    toast_('已标记：' + b.textContent);
    close();
  });
  setTimeout(() => document.addEventListener('click', close, { once: true }), 0);
}

/* 精确选区：捕获选中文本 + UTF-16 偏移（相对目标 Block） */
function captureSelection(block) {
  const s = window.getSelection();
  if (!s.rangeCount) return null;
  const r = s.getRangeAt(0);
  const text = r.toString();
  if (!text || !block.contains(r.startContainer)) return null;
  const pre = document.createRange();
  pre.selectNodeContents(block);
  pre.setEnd(r.startContainer, r.startOffset);
  return { text, start_utf16: pre.toString().length, end_utf16: pre.toString().length + text.length };
}

/* 工具请求统一锚点（问题发生在哪里，结果就近呈现） */
function anchorFor(target, selection) {
  return {
    article_id: currentAid,
    target_block_id: target.dataset.bid || null,
    selection: selection || null,
  };
}

async function runRewrite(target) {
  if (!requireCfg()) return;
  target = target || firstBlock();
  if (!target) { toast_('先写点什么再改写'); return; }
  const sel = captureSelection(target); // 精确选中文字（无选中则整段）
  const text = (sel ? sel.text : target.textContent.trim());
  if (!text) { toast_('这一段还是空的'); return; }
  const card = document.createElement('div');
  card.className = 'ai-card';
  card.innerHTML = '<div class="ai-head">正在改写…</div>';
  target.after(card);
  try {
    const r = await api('/api/ai/rewrite', {
      method: 'POST',
      body: JSON.stringify({ text: text.slice(0, 2000), ...anchorFor(target, sel) }),
    });
    card.innerHTML = '<div class="ai-head">改写候选</div>'
      + r.candidates.map(c => `<div class="opt"><span class="tag">${escapeHtml(c.label)}</span>${escapeHtml(c.text)}</div>`).join('')
      + '<div class="acts"><button class="btn btn-g" data-x="rej">拒绝</button><button class="btn btn-p" data-x="acc">接受方案一</button></div>';
    card.querySelector('[data-x="rej"]').onclick = () => { reportSignal('reject', { focus: 'block' }); card.remove(); };
    card.querySelector('[data-x="acc"]').onclick = () => {
      pushUndo(currentAid); // 可撤销点（WEN-023）
      reportSignal('accept', { focus: 'block' });
      const newText = r.candidates[0].text;
      if (sel && sel.start_utf16 !== undefined) {
        applySelectionToBlock(target, sel, newText); // 精确替换选中文字
      } else {
        target.innerHTML = `<mark class="ins">${escapeHtml(newText)}</mark>`;
        setTimeout(() => target.querySelector('mark').classList.add('fade'), 600);
      }
      card.remove();
      markDirty(currentAid); // 改动标记（AI reason 保存）
      saveNow(currentAid, 'ai_rewrite');
      toast_('已接受（⌘Z 可撤销）');
    };
  } catch (e) {
    card.innerHTML = '<div class="ai-head">改写失败：' + escapeHtml(e.message) + '</div>';
  }
}
$('#tool-rw').addEventListener('click', () => { reportSignal('tool_click', { tool: 'rewrite', focus: 'block' }); runRewrite(anchorFromSel()); });
$('#ask-send').addEventListener('click', () => { reportSignal('tool_click', { tool: 'ask', focus: 'article' }); });
$('#ask-input').addEventListener('keydown', e => { if (e.key === 'Enter') reportSignal('tool_click', { tool: 'ask', focus: 'article' }); });

/* ========== 洞察链路（手动触发，安全默认：打开草稿不自动调用模型） ========== */
function showInsightIdle() {
  const was = $('#cardflow').innerHTML;
  $('#cardflow').innerHTML = `
    <div class="insight">
      <div class="ins-head"><span class="ins-ic">◎</span><span class="ins-t">当前洞察</span></div>
      <div class="ins-row"><span class="v" style="width:auto">AI 理解这篇文章，需要把正文发送给模型。点下面按钮手动生成。</span></div>
      <div class="acts"><button class="btn btn-p" id="btn-insight">生成洞察</button></div>
    </div>`;
  $('#btn-insight').addEventListener('click', () => loadInsight(currentAid));
}

async function loadInsight(aid) {
  if (insightAbort) insightAbort.abort();
  const abort = new AbortController();
  insightAbort = abort;
  const seq = articleReqSeq;
  if (!cfg.configured) {
    toast_('请先在设置里配置模型');
    return;
  }
  $('#cardflow').innerHTML = `
    <div class="insight">
      <div class="ins-head"><span class="ins-ic">◎</span><span class="ins-t">当前洞察</span><span class="ins-badge">AI 正在读</span></div>
      <div class="ins-row"><span class="v" style="width:auto">正在分析这篇文章…</span></div>
    </div>`;
  try {
    const a = await api(`/api/articles/${aid}`);
    if (seq !== articleReqSeq) return; // 已切稿
    const resp = await fetch('/api/ai/insight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: a.title, blocks: a.blocks }),
      signal: abort.signal,
    });
    if (seq !== articleReqSeq) return;
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.detail || ('HTTP ' + resp.status));
    }
    renderInsight(await resp.json());
  } catch (e) {
    if (e.name === 'AbortError' || seq !== articleReqSeq) return; // 取消/切稿：静默
    $('#cardflow').innerHTML = `<div class="insight"><div class="ins-head"><span class="ins-t">洞察</span></div><div class="ins-row"><span class="v" style="width:auto">${escapeHtml(e.message)}</span></div></div>`;
  }
}

function renderInsight(r) {
  const ins = r.insight || {};
  const sugs = r.suggestions || [];
  const iconFor = a => a === 'search'
    ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg>'
    : a === 'check'
      ? '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 4.5L6.5 11 3 7.5"/><circle cx="8" cy="8" r="6.5"/></svg>'
      : '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2l3 3L5.5 13.5 2 14l.5-3.5L11 2z"/></svg>';
  const labelFor = a => a === 'search' ? '搜索' : a === 'check' ? '核验' : '改写';
  $('#cardflow').innerHTML = `
    <div class="insight">
      <div class="ins-head"><span class="ins-ic">◎</span><span class="ins-t">当前洞察</span><span class="ins-badge">AI 正在读</span></div>
      <div class="ins-row"><span class="k">这段在说</span><span class="v">${escapeHtml(ins.summary || '—')}</span></div>
      <div class="ins-row"><span class="k">缺什么</span><span class="v">${escapeHtml(ins.gap || '—')}</span></div>
    </div>
    ${sugs.length ? `<div class="sug-head">建议 <span class="cnt">${sugs.length}</span></div><div class="sug">${sugs.map(s => `
      <div class="sug-item" data-act="${escapeHtml(s.action)}">
        <span class="s-ic">${iconFor(s.action)}</span>
        <div class="s-main"><div class="s-t">${escapeHtml(s.title)}</div><div class="s-d">${escapeHtml(s.desc)}</div></div>
        <button class="mini2">${labelFor(s.action)}</button>
      </div>`).join('')}</div>` : ''}
  `;
  $('#cardflow').querySelectorAll('.sug-item').forEach(item => item.addEventListener('click', () => {
    const act = item.dataset.act;
    if (act === 'rewrite') runRewrite(firstBlock());
    else if (act === 'search') runSearch(firstBlock());
    else if (act === 'check') runCheck(firstBlock());
  }));
}

/* 版本历史：AI 改写/核验采纳/手动恢复会记录版本，可一键恢复 */
async function showHistory() {
  if (!currentAid) return;
  try {
    const r = await api(`/api/articles/${currentAid}/revisions`);
    const revs = r.revisions || [];
    if (!revs.length) { toast_('还没有历史版本（AI 改写/核验采纳时自动记录）'); return; }
    const card = document.createElement('div');
    card.className = 'ai-card';
    const reasonLabel = v => ({ ai_rewrite: 'AI 改写', ai_check: '核验修订', restore: '版本恢复', autosave: '自动保存' }[v] || v);
    card.innerHTML = '<div class="ai-head">版本历史 <button class="mini2" id="hist-close" style="float:right">关闭</button></div>'
      + revs.map(v => `<div class="res">
          <div class="t">v${v.version} · ${reasonLabel(v.reason)}</div>
          <div class="sn">${escapeHtml(String(v.created_at).replace('T', ' ').slice(0, 16))}</div>
          <div class="res-acts"><button class="mini2" data-x="restore" data-v="${v.version}">恢复此版本</button></div>
        </div>`).join('');
    $('#cardflow').prepend(card);
    $('#hist-close').onclick = () => card.remove();
    card.querySelectorAll('[data-x="restore"]').forEach(b => b.onclick = async () => {
      if (!confirm(`恢复到 v${b.dataset.v}？（当前内容会保留为历史）`)) return;
      pushUndo(currentAid); // 可撤销点（WEN-023）
      await api(`/api/articles/${currentAid}/revisions/${b.dataset.v}/restore`, { method: 'POST' });
      card.remove();
      openArticle(currentAid);
      toast_('已恢复到 v' + b.dataset.v);
    });
  } catch (e) { toast_('历史加载失败：' + e.message); }
}

/* 精确选区替换：只替换选中文字（UTF-16 偏移），其余保留 */
function applySelectionToBlock(block, sel, newText) {
  const raw = block.textContent;
  block.textContent = raw; // 归一为单文本节点（旧内容含 mark 包装时）
  const node = block.firstChild;
  const start = Math.min(sel.start_utf16, raw.length);
  const end = Math.min(sel.end_utf16, raw.length);
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, end);
  range.deleteContents();
  range.insertNode(document.createTextNode(newText));
  // 光标移到插入后
  const s = window.getSelection();
  s.removeAllRanges();
  const r2 = document.createRange();
  r2.setStart(node, Math.min(start + newText.length, node.textContent.length));
  r2.collapse(true);
  s.addRange(r2);
}

/* 引用编号渲染：由文章 citations 计算，badge 不写入正文（保存时无 [N] 污染） */
async function renderCitationBadges() {
  if (!currentAid) return;
  try {
    const r = await api(`/api/articles/${currentAid}/citations`);
    const cites = r.citations.filter(c => c.status !== 'orphaned');
    document.querySelectorAll('#article sup.cite').forEach(s => s.remove());
    cites.forEach((c, i) => {
      const block = document.querySelector(`#article .blk.edit[data-bid="${c.block_id}"]`);
      if (block) {
        const sup = document.createElement('sup');
        sup.className = 'cite';
        sup.textContent = '[' + (i + 1) + ']';
        sup.title = (c.source_title || '来源') + (c.source_url ? ' · ' + c.source_url : '');
        block.appendChild(sup);
      }
    });
  } catch { /* 引用列表失败不阻断写作 */ }
}

/* 保存来源（复用同 url）+ 返回 source id */
async function ensureSource(res, provider) {
  const r = await api(`/api/projects/${currentPid}/sources`, {
    method: 'POST',
    body: JSON.stringify({ url: res.url || '', title: res.title, snippet: res.snippet, provider }),
  });
  return r.id;
}

/* ========== 搜索链路（真搜索：Wikipedia / DuckDuckGo，降级模型知识） ========== */
async function runSearch(target) {
  target = target || firstBlock();
  if (!target) { toast_('先写点什么再搜索'); return; }
  const sel = captureSelection(target); // 精确选中文字（无选中则整段）
  const q = (sel ? sel.text : target.textContent.trim()).slice(0, 200);
  if (!q) { toast_('这一段还是空的'); return; }
  const card = document.createElement('div');
  card.className = 'ai-card';
  card.innerHTML = '<div class="ai-head">搜索中…</div><div class="opt">联网检索 + 模型整理资料线索，首次可能需要一点时间</div>';
  target.after(card);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 75000);
  try {
    const resp = await fetch('/api/ai/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, ...anchorFor(target, sel), stream: true }),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.detail || ('HTTP ' + resp.status));
    }
    // NDJSON 流式：stage → result，渐进渲染
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let results = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        let ev;
        try { ev = JSON.parse(line); } catch { continue; }
        if (ev.type === 'stage' && ev.stage === 'fetching') {
          card.innerHTML = '<div class="ai-head">搜索中…</div><div class="opt">正在联网检索（外网受限时自动改用模型知识）…</div>';
        }
        if (ev.type === 'result') results = ev.results || [];
      }
    }
    if (!results.length) { card.innerHTML = '<div class="ai-head">没有找到相关资料</div>'; return; }
    card.innerHTML = '<div class="ai-head">搜索结果 · ' + results.length + ' 条</div>' +
      results.map((res, i) => `<div class="res">
        <div class="t">${escapeHtml(res.title)} <span class="src ${res.source === 'web' ? 'web' : ''}">${res.source === 'web' ? '已检索' : '模型知识'}</span></div>
        <div class="sn">${escapeHtml(res.snippet)}</div>
        <div class="res-acts"><button class="mini2" data-x="cite">引用</button><button class="mini2" data-x="save">存入素材</button>
        ${(res.url && safeUrl(res.url)) ? `<a class="mini2 link" href="${escapeHtml(safeUrl(res.url))}" target="_blank" rel="noopener noreferrer">打开</a>` : ''}</div>
      </div>`).join('');
    card.querySelectorAll('[data-x="cite"]').forEach((b, i) => b.onclick = async () => {
      const res = results[i];
      const quote = (sel ? sel.text : target.textContent.trim()).slice(0, 200);
      try {
        const sid = await ensureSource(res, res.source || 'model');
        await api(`/api/articles/${currentAid}/citations`, {
          method: 'POST',
          body: JSON.stringify({ block_id: target.dataset.bid, source_id: sid, quote, display_label: res.title.slice(0, 60) }),
        });
        await renderCitationBadges();
        toast_('已引用：' + (res.title || '来源'));
      } catch (e) { toast_('引用失败：' + e.message); }
    });
    card.querySelectorAll('[data-x="save"]').forEach((b, i) => b.onclick = async () => {
      const res = results[i];
      try {
        const sid = await ensureSource(res, res.source || 'model');
        await api(`/api/projects/${currentPid}/materials`, {
          method: 'POST',
          body: JSON.stringify({ title: res.title.slice(0, 80), content: res.snippet, source_id: sid }),
        });
        toast_('已存入项目素材库');
      } catch (e) { toast_('存入失败：' + e.message); }
    });
  } catch (e) {
    clearTimeout(timer);
    card.innerHTML = '<div class="ai-head">' + (e.name === 'AbortError' ? '搜索超时（75 秒）' : '搜索失败：' + escapeHtml(e.message)) + '</div>';
  }
}

/* ========== 核验链路（LLM 三态：可信 / 存疑 / 建议修改） ========== */
async function runCheck(target) {
  if (!requireCfg()) return;
  target = target || firstBlock();
  if (!target) { toast_('先选中要核验的内容'); return; }
  const sel = captureSelection(target); // 精确选中文字（无选中则整段）
  const claim = (sel ? sel.text : target.textContent.trim()).slice(0, 500);
  if (!claim) { toast_('内容为空'); return; }
  const card = document.createElement('div');
  card.className = 'ai-card';
  card.innerHTML = '<div class="ai-head">核验中…</div><div class="opt">模型正在核查这条陈述</div>';
  target.after(card);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  try {
    const resp = await fetch('/api/ai/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ claim, ...anchorFor(target, sel) }),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.detail || ('HTTP ' + resp.status));
    }
    const r = await resp.json();
    const label = { ok: '可信', doubt: '存疑', fix: '建议修改' }[r.status] || '存疑';
    const cls = r.status === 'ok' ? 'ok' : r.status === 'fix' ? 'fix' : 'doubt';
    const evs = (r.evidence || []);
    const evHtml = evs.length
      ? '<div class="ev-head">证据（抓取自' + (evs.length === 1 ? '' : ' ' + evs.length + ' 个') + '来源）</div>' +
        evs.map(e => `<div class="ev"><a href="${escapeHtml(safeUrl(e.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(e.title || e.url)}</a></div>`).join('')
      : '<div class="ev-head">证据</div><div class="ev none">未能抓取到可核验来源——当前判断基于模型知识，建议手动查证</div>';
    card.innerHTML = `<div class="ai-head">事实核验</div>
      <div class="vc ${cls}"><span class="l">${label}</span><span class="r">${escapeHtml(r.reason)}</span></div>
      ${r.suggestion ? `<div class="opt"><span class="tag">建议改为</span>${escapeHtml(r.suggestion)}</div>` : ''}
      ${evHtml}
      <div class="acts">${r.suggestion
        ? '<button class="btn btn-g" data-x="rej">忽略</button><button class="btn btn-p" data-x="acc">采用建议</button>'
        : '<button class="btn btn-g" data-x="rej">知道了</button>'}</div>`;
    card.querySelector('[data-x="rej"]').onclick = () => { reportSignal('reject', { focus: 'block' }); card.remove(); };
    const acc = card.querySelector('[data-x="acc"]');
    if (acc) acc.onclick = () => {
      pushUndo(currentAid); // 可撤销点（WEN-023）
      reportSignal('accept', { focus: 'block' });
      if (sel && sel.start_utf16 !== undefined) {
        applySelectionToBlock(target, sel, r.suggestion); // 精确替换选中文字
      } else {
        target.innerHTML = `<mark class="ins">${escapeHtml(r.suggestion)}</mark>`;
        setTimeout(() => target.querySelector('mark').classList.add('fade'), 600);
      }
      card.remove();
      markDirty(currentAid); // 改动标记（AI reason 保存）
      saveNow(currentAid, 'ai_check');
      toast_('已按建议修订（⌘Z 可撤销）');
    };
  } catch (e) {
    clearTimeout(timer);
    card.innerHTML = '<div class="ai-head">' + (e.name === 'AbortError' ? '核验超时（60 秒）' : '核验失败：' + escapeHtml(e.message)) + '</div>';
  }
}

/* 搜索/核验 工具坞（真链路 + 信号上报） */
$('#tool-sr').addEventListener('click', () => { reportSignal('tool_click', { tool: 'search', focus: 'block' }); runSearch(anchorFromSel()); });
$('#tool-ck').addEventListener('click', () => { reportSignal('tool_click', { tool: 'check', focus: 'block' }); runCheck(anchorFromSel()); });

/* 关闭/隐藏前 best-effort flush；可靠性由 IndexedDB 恢复副本 + 正常保存保证 */
window.addEventListener('pagehide', () => {
  if (currentAid) {
    cancelPendingSave();
    const st = sstate(currentAid);
    if (st.dirty) saveNow(currentAid);
  }
});

/* ========== 窄屏抽屉（互斥：项目 / 助手） ========== */
const mask = $('#drawer-mask');
let drawerOpen = null; // 'proj' | 'helper' | null

function openDrawer(which) {
  if (drawerOpen === which) { closeDrawer(); return; }
  closeDrawer();
  drawerOpen = which;
  document.body.classList.add('drawer-' + which, 'drawer-open');
  mask.style.display = 'block';
  const target = which === 'proj' ? $('#btn-drawer-proj') : $('#btn-drawer-helper');
  if (target) target.focus();
}

function closeDrawer() {
  document.body.classList.remove('drawer-proj', 'drawer-helper', 'drawer-open');
  mask.style.display = 'none';
  drawerOpen = null;
}

$('#btn-drawer-proj').addEventListener('click', () => openDrawer('proj'));
$('#btn-drawer-helper').addEventListener('click', () => openDrawer('helper'));
mask.addEventListener('click', closeDrawer);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
window.addEventListener('resize', () => { if (window.innerWidth >= 1100) closeDrawer(); });

/* 启动时获取随机 session token（HttpOnly cookie，后续写请求自动携带） */
fetch('/api/session').catch(() => {});

loadProjects();
loadSettings();
