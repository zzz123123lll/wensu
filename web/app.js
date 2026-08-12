/* 文序 · 前端逻辑（第一阶段：真数据 + Block 编辑 + 自动保存） */
'use strict';

const $ = s => document.querySelector(s);
const toast = $('#toast');
let projects = [];          // [{id, name}]
let expanded = {};          // pid -> bool
let currentAid = null;      // 当前打开草稿 id
let currentPid = null;

function toast_(m) {
  toast.textContent = m;
  toast.classList.add('show');
  clearTimeout(toast_._t);
  toast_._t = setTimeout(() => toast.classList.remove('show'), 1800);
}

async function api(path, opts = {}) {
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
        <span class="arr">▶</span><span>${p.name}</span><span class="cnt" data-cnt="${p.id}"></span>
      </div>
      <div class="doc sub newdraft" data-pid="${p.id}" data-new="1" style="display:${open ? '' : 'none'}">＋ 新建草稿</div>
      <div class="docs-${p.id}" style="display:${open ? '' : 'none'}"></div>
    `;
  }).join('');

  $('#side-list').querySelectorAll('.proj').forEach(proj => proj.addEventListener('click', async () => {
    const pid = +proj.dataset.pid;
    expanded[pid] = !expanded[pid];
    renderSide();
    if (expanded[pid]) await loadArticles(pid);
  }));
  $('#side-list').querySelectorAll('.doc[data-new]').forEach(row => row.addEventListener('click', e => {
    e.stopPropagation();
    inlineName('草稿标题', async name => {
      const pid = +row.dataset.pid;
      await api(`/api/projects/${pid}/articles`, { method: 'POST', body: JSON.stringify({ title: name }) });
      await loadArticles(pid);
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
    </div>`).join('');
  box.querySelectorAll('.doc[data-aid]').forEach(d => d.addEventListener('click', () => openArticle(+d.dataset.aid)));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
  // 切稿前 flush 旧稿：清 timer + 立即保存旧稿（per-aid 状态，不会写错稿）
  if (currentAid && currentAid !== aid) {
    clearTimeout(saveTimer);
    const old = sstate(currentAid);
    if (old.dirty) saveNow(currentAid);
  }
  currentAid = aid;
  api(`/api/articles/${aid}`).then(a => {
    currentPid = a.project_id;
    sstate(aid).baseVersion = a.version || 1;
    $('#empty').style.display = 'none';
    const art = $('#article');
    art.classList.add('show');
    $('#doc-title').textContent = a.title;

    let html = `<div class="art-title">${escapeHtml(a.title)}</div>`;
    html += `<div class="art-meta">草稿 · <span id="save-status" class="save-status"></span></div>`;
    if (a.blocks.length === 0) {
      html += `<div class="blk edit empty" contenteditable="true" data-bid="${crypto.randomUUID()}"></div>`;
    } else {
      html += a.blocks.map(b => blockHtml(b)).join('');
    }
    art.innerHTML = html;
    bindEditor();
    // 高亮左栏当前草稿
    document.querySelectorAll('.doc[data-aid]').forEach(d => d.classList.toggle('active', +d.dataset.aid === aid));
    $('#doc-scroll').scrollTop = 0;
    loadInsight(aid);
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

/* ========== 保存状态机（每草稿独立状态，防串稿/丢稿） ========== */
const saveStates = new Map();  // aid -> state
let saveTimer = null;

function sstate(aid) {
  if (!saveStates.has(aid)) {
    saveStates.set(aid, {
      aid,
      baseVersion: 1,
      editRevision: 0,
      ackedRevision: 0,
      snapshotHash: '',
      inFlight: null,        // {aid, revision, hash}
      pendingAfterSave: false,
      dirty: false,
      status: 'clean',       // clean|dirty|saving|saved|conflict|offline|recovery-failed
    });
  }
  return saveStates.get(aid);
}

function domHash() {
  // 当前正文的轻量 hash（用于 ACK 校验快照是否仍是最新）
  let h = 0;
  for (const b of document.querySelectorAll('#article .blk.edit')) {
    for (const ch of b.textContent) h = (h * 31 + ch.codePointAt(0)) | 0;
  }
  return (h >>> 0).toString(36);
}

const SAVE_LABEL = {
  clean: '', dirty: '未保存', saving: '保存中…', saved: '已保存',
  conflict: '冲突', offline: '离线待重试', 'recovery-failed': '本地副本未建立',
};

function setSaveStatus(st, status) {
  st.status = status;
  const el = $('#save-status');
  if (el) {
    const label = SAVE_LABEL[status] || status;
    el.textContent = label;
    el.className = 'save-status ' + status;
    if (status === 'saved' || status === 'clean') {
      clearTimeout(setSaveStatus._t);
      setSaveStatus._t = setTimeout(() => { el.textContent = ''; el.className = 'save-status'; }, 2000);
    }
  }
}

/* ---------- IndexedDB 恢复副本 ---------- */
const RECOVERY_DB = 'wensu-recovery';
const RECOVERY_STORE = 'drafts';
function openRecoveryDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(RECOVERY_DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(RECOVERY_STORE, { keyPath: 'article_id' });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
function writeRecovery(aid, baseVersion, snapshot, editRevision, hash) {
  return openRecoveryDb().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(RECOVERY_STORE, 'readwrite');
    tx.objectStore(RECOVERY_STORE).put({
      article_id: aid, base_version: baseVersion, snapshot,
      edit_revision: editRevision, snapshot_hash: hash, queued_at: Date.now(),
    });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  }));
}
function clearRecovery(aid) {
  return openRecoveryDb().then(db => new Promise((resolve, reject) => {
    const tx = db.transaction(RECOVERY_STORE, 'readwrite');
    tx.objectStore(RECOVERY_STORE).delete(aid);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  }));
}

/* ---------- 事件委托（Enter 新建块后输入仍触发保存） ---------- */
function bindEditor() {
  const art = $('#article');
  art.addEventListener('keydown', e => {
    const block = e.target.closest ? e.target.closest('.blk.edit') : null;
    if (!block) return;
    if (e.key === 'Enter' && !e.shiftKey) {
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
}

function markDirty(aid) {
  if (!aid) return;
  const st = sstate(aid);
  st.dirty = true;
  st.editRevision += 1;
  st.snapshotHash = domHash();
  setSaveStatus(st, 'dirty');
  scheduleSave(aid);
}

function scheduleSave(aid) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveNow(aid), 1200);
}

async function saveNow(aid, reason = 'autosave') {
  const st = sstate(aid);
  if (!st.dirty) return;
  if (st.inFlight) { st.pendingAfterSave = true; return; } // 在途：标记，ACK 后发最新
  const snapshot = collectBlocks();
  st.dirty = false;
  st.inFlight = { aid, revision: st.editRevision, hash: st.snapshotHash };
  setSaveStatus(st, 'saving');
  // 先写 IndexedDB 恢复副本（失败/超时不得阻塞保存，但不得谎报"已保存"）
  let recoveryOk = false;
  try {
    await Promise.race([
      writeRecovery(aid, st.baseVersion, snapshot, st.editRevision, st.snapshotHash),
      new Promise((_, rej) => setTimeout(() => rej(new Error('IndexedDB 超时')), 800)),
    ]);
    recoveryOk = true;
  } catch {
    setSaveStatus(st, 'recovery-failed');
    toast_('本地恢复副本未建立，请勿关闭页面');
  }
  try {
    const resp = await fetch(`/api/articles/${aid}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ blocks: snapshot, base_version: st.baseVersion, change_reason: reason }),
    });
    if (resp.status === 409) {
      const d = await resp.json().catch(() => ({}));
      st.inFlight = null;
      st.dirty = false;
      setSaveStatus(st, 'conflict');
      showConflict(aid, (d.detail && d.detail.current_version) || st.baseVersion);
      return;
    }
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();
    st.baseVersion = d.version;
    // 只有 ACK 对应 revision/hash 等于当前，才显示"已保存"
    if (st.inFlight && st.inFlight.revision === st.editRevision && st.inFlight.hash === st.snapshotHash) {
      st.ackedRevision = st.editRevision;
      setSaveStatus(st, recoveryOk ? 'saved' : 'saved');
      if (!recoveryOk) toast_('已保存，但本地恢复副本未建立');
    }
    st.inFlight = null;
    clearRecovery(aid).catch(() => {});
    if (st.pendingAfterSave) {
      st.pendingAfterSave = false;
      st.dirty = true;
      scheduleSave(aid);
    }
  } catch (e) {
    st.inFlight = null;
    st.dirty = true;
    setSaveStatus(st, 'offline');
    toast_('保存失败，稍后自动重试：' + e.message);
    scheduleSave(aid); // 重试最新快照
  }
}

/* 409 冲突：本地快照已在 IndexedDB，服务端内容保留，给出恢复动作 */
function showConflict(aid, serverVersion) {
  const el = $('#save-status');
  if (!el) return;
  el.textContent = '冲突';
  el.className = 'save-status conflict';
  el.innerHTML = '冲突 <button class="mini2" id="conflict-local">保留本地</button><button class="mini2" id="conflict-server">用服务器版</button>';
  $('#conflict-local').onclick = () => {
    openRecoveryDb().then(db => new Promise(res => {
      const r = db.transaction(RECOVERY_STORE).objectStore(RECOVERY_STORE).get(aid);
      r.onsuccess = () => res(r.result);
    })).then(rec => {
      if (rec) { renderBlocks(rec.snapshot); const st = sstate(aid); st.baseVersion = serverVersion; st.editRevision += 1; saveNow(aid); toast_('已恢复本地副本并重新保存'); }
      else toast_('未找到本地副本');
    });
  };
  $('#conflict-server').onclick = () => openArticle(aid);
}

function renderBlocks(blocks) {
  $('#article').innerHTML = `<div class="art-title">${escapeHtml($('#doc-title').textContent)}</div>` +
    blocks.map(b => {
      if (b.type === 'heading') return `<h2 class="blk edit" contenteditable="true" data-bid="${escapeHtml(b.id)}">${escapeHtml(b.text)}</h2>`;
      if (b.type === 'blockquote') return `<blockquote class="blk edit" contenteditable="true" data-bid="${escapeHtml(b.id)}">${escapeHtml(b.text)}</blockquote>`;
      return `<div class="blk edit ${b.text ? '' : 'empty'}" contenteditable="true" data-bid="${escapeHtml(b.id)}">${escapeHtml(b.text)}</div>`;
    }).join('');
}

function collectBlocks() {
  return Array.from(document.querySelectorAll('#article .blk.edit')).map((d, i) => ({
    id: d.dataset.bid, // 稳定 ID（Enter 时已生成 UUID；旧数据保留原 ID）
    type: d.tagName === 'H2' ? 'heading' : (d.tagName === 'BLOCKQUOTE' ? 'blockquote' : 'paragraph'),
    text: d.textContent,
    attrs: {},
  }));
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

/* ========== 模型设置 ========== */
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
    const r = await api('/api/ai/ask', { method: 'POST', body: JSON.stringify({ prompt: t, context: ctx }) });
    busy.remove();
    addPanelCard('回答', r.reply);
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

async function runRewrite(target) {
  if (!requireCfg()) return;
  target = target || firstBlock();
  if (!target) { toast_('先写点什么再改写'); return; }
  const text = target.textContent.trim();
  if (!text) { toast_('这一段还是空的'); return; }
  const card = document.createElement('div');
  card.className = 'ai-card';
  card.innerHTML = '<div class="ai-head">正在改写…</div>';
  target.after(card);
  try {
    const r = await api('/api/ai/rewrite', { method: 'POST', body: JSON.stringify({ text: text.slice(0, 2000) }) });
    card.innerHTML = '<div class="ai-head">改写候选</div>'
      + r.candidates.map(c => `<div class="opt"><span class="tag">${escapeHtml(c.label)}</span>${escapeHtml(c.text)}</div>`).join('')
      + '<div class="acts"><button class="btn btn-g" data-x="rej">拒绝</button><button class="btn btn-p" data-x="acc">接受方案一</button></div>';
    card.querySelector('[data-x="rej"]').onclick = () => card.remove();
    card.querySelector('[data-x="acc"]').onclick = () => {
      const newText = r.candidates[0].text;
      target.innerHTML = `<mark class="ins">${escapeHtml(newText)}</mark>`;
      setTimeout(() => target.querySelector('mark').classList.add('fade'), 600);
      card.remove();
      saveNow(currentAid, 'ai_rewrite');
      toast_('已接受，改动已保存');
    };
  } catch (e) {
    card.innerHTML = '<div class="ai-head">改写失败：' + escapeHtml(e.message) + '</div>';
  }
}
$('#tool-rw').addEventListener('click', () => runRewrite(anchorFromSel()));

/* ========== 洞察链路 ========== */
async function loadInsight(aid) {
  if (!cfg.configured) {
    $('#cardflow').innerHTML = `
      <div class="insight">
        <div class="ins-head"><span class="ins-ic">◎</span><span class="ins-t">写作助手</span><span class="ins-badge">未启用</span></div>
        <div class="ins-row"><span class="v" style="width:auto">先在右上角 ⚙ 设置你的 API Key 和模型，AI 建议就会出现在这里。</span></div>
      </div>`;
    return;
  }
  try {
    const a = await api(`/api/articles/${aid}`);
    const r = await api('/api/ai/insight', { method: 'POST', body: JSON.stringify({ title: a.title, blocks: a.blocks }) });
    renderInsight(r);
  } catch (e) {
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

/* ========== 搜索链路（真搜索：Wikipedia / DuckDuckGo，降级模型知识） ========== */
async function runSearch(target) {
  target = target || firstBlock();
  if (!target) { toast_('先写点什么再搜索'); return; }
  const q = target.textContent.trim().slice(0, 200);
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
      body: JSON.stringify({ query: q }),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.detail || ('HTTP ' + resp.status));
    }
    const r = await resp.json();
    const results = r.results || [];
    if (!results.length) { card.innerHTML = '<div class="ai-head">没有找到相关资料</div>'; return; }
    card.innerHTML = '<div class="ai-head">搜索结果 · ' + results.length + ' 条</div>' +
      results.map((res, i) => `<div class="res">
        <div class="t">${escapeHtml(res.title)} <span class="src ${res.source === 'web' ? 'web' : ''}">${res.source === 'web' ? '已检索' : '模型知识'}</span></div>
        <div class="sn">${escapeHtml(res.snippet)}</div>
        <div class="res-acts"><button class="mini2" data-x="cite">引用</button>
        ${res.url ? `<a class="mini2 link" href="${escapeHtml(res.url)}" target="_blank" rel="noopener">打开</a>` : ''}</div>
      </div>`).join('');
    card.querySelectorAll('[data-x="cite"]').forEach((b, i) => b.onclick = () => {
      const sup = document.createElement('sup');
      sup.style.cssText = 'color:var(--accent);font-weight:700;font-size:11px;margin-left:1px;';
      sup.textContent = '[' + (i + 1) + ']';
      target.appendChild(sup);
      scheduleSave();
      toast_('已插入引用标记 [' + (i + 1) + ']');
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
  const claim = target.textContent.trim().slice(0, 500);
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
      body: JSON.stringify({ claim }),
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
    card.innerHTML = `<div class="ai-head">事实核验</div>
      <div class="vc ${cls}"><span class="l">${label}</span><span class="r">${escapeHtml(r.reason)}</span></div>
      ${r.suggestion ? `<div class="opt"><span class="tag">建议改为</span>${escapeHtml(r.suggestion)}</div>` : ''}
      <div class="acts">${r.suggestion
        ? '<button class="btn btn-g" data-x="rej">忽略</button><button class="btn btn-p" data-x="acc">采用建议</button>'
        : '<button class="btn btn-g" data-x="rej">知道了</button>'}</div>`;
    card.querySelector('[data-x="rej"]').onclick = () => card.remove();
    const acc = card.querySelector('[data-x="acc"]');
    if (acc) acc.onclick = () => {
      target.innerHTML = `<mark class="ins">${escapeHtml(r.suggestion)}</mark>`;
      setTimeout(() => target.querySelector('mark').classList.add('fade'), 600);
      card.remove();
      saveNow(currentAid, 'ai_check');
      toast_('已按建议修订');
    };
  } catch (e) {
    clearTimeout(timer);
    card.innerHTML = '<div class="ai-head">' + (e.name === 'AbortError' ? '核验超时（60 秒）' : '核验失败：' + escapeHtml(e.message)) + '</div>';
  }
}

/* 搜索/核验 工具坞（真链路） */
$('#tool-sr').addEventListener('click', () => runSearch(anchorFromSel()));
$('#tool-ck').addEventListener('click', () => runCheck(anchorFromSel()));

/* 关闭/隐藏前 best-effort flush；可靠性由 IndexedDB 恢复副本 + 正常保存保证 */
window.addEventListener('pagehide', () => {
  if (currentAid) {
    clearTimeout(saveTimer);
    const st = sstate(currentAid);
    if (st.dirty) saveNow(currentAid);
  }
});

loadProjects();
loadSettings();
