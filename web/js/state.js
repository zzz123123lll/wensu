// 保存状态机：每草稿独立状态，防串稿/丢稿；IndexedDB 恢复副本；409 双份。
// 依赖：security.js 的 escapeHtml（renderBlocks 用）

import { escapeHtml } from './security.js';

export const $ = s => document.querySelector(s);

export function toast_(m) {
  const toast = $('#toast');
  toast.textContent = m;
  toast.classList.add('show');
  clearTimeout(toast_._t);
  toast_._t = setTimeout(() => toast.classList.remove('show'), 1800);
}

const saveStates = new Map();  // aid -> state
let saveTimer = null;

export function sstate(aid) {
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

export function cancelPendingSave() { clearTimeout(saveTimer); }

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

export function markDirty(aid) {
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

export async function saveNow(aid, reason = 'autosave') {
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
      setSaveStatus(st, 'saved');
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
export function showConflict(aid, serverVersion) {
  const el = $('#save-status');
  if (!el) return;
  el.innerHTML = '冲突 <button class="mini2" id="conflict-local">保留本地</button><button class="mini2" id="conflict-server">用服务器版</button>';
  el.className = 'save-status conflict';
  $('#conflict-local').onclick = () => {
    openRecoveryDb().then(db => new Promise(res => {
      const r = db.transaction(RECOVERY_STORE).objectStore(RECOVERY_STORE).get(aid);
      r.onsuccess = () => res(r.result);
    })).then(rec => {
      if (rec) {
        renderBlocks(rec.snapshot);
        const st = sstate(aid);
        st.baseVersion = serverVersion;
        st.editRevision += 1;
        saveNow(aid);
        toast_('已恢复本地副本并重新保存');
      } else toast_('未找到本地副本');
    });
  };
  $('#conflict-server').onclick = () => location.reload();
}

export function collectBlocks() {
  return Array.from(document.querySelectorAll('#article .blk.edit')).map(d => ({
    id: d.dataset.bid, // 稳定 ID（Enter 时已生成 UUID；旧数据保留原 ID）
    type: d.tagName === 'H2' ? 'heading' : (d.tagName === 'BLOCKQUOTE' ? 'blockquote' : 'paragraph'),
    text: d.textContent,
    attrs: {},
  }));
}

/* ---------- 撤销栈（每草稿；AI 应用/版本恢复前入栈，Ctrl+Z 出栈） ---------- */
const undoStacks = new Map();  // aid -> [{blocks}]

export function pushUndo(aid) {
  if (!aid) return;
  const st = undoStacks.get(aid) || [];
  st.push({ blocks: collectBlocks(), ts: Date.now() });
  if (st.length > 50) st.shift();
  undoStacks.set(aid, st);
}

export function popUndo(aid) {
  const st = undoStacks.get(aid);
  if (!st || !st.length) return null;
  const item = st.pop();
  if (!st.length) undoStacks.delete(aid);
  return item.blocks;
}

export function renderBlocks(blocks) {
  $('#article').innerHTML = `<div class="art-title">${escapeHtml($('#doc-title').textContent)}</div>` +
    blocks.map(b => {
      if (b.type === 'heading') return `<h2 class="blk edit" contenteditable="true" data-bid="${escapeHtml(b.id)}">${escapeHtml(b.text)}</h2>`;
      if (b.type === 'blockquote') return `<blockquote class="blk edit" contenteditable="true" data-bid="${escapeHtml(b.id)}">${escapeHtml(b.text)}</blockquote>`;
      return `<div class="blk edit ${b.text ? '' : 'empty'}" contenteditable="true" data-bid="${escapeHtml(b.id)}">${escapeHtml(b.text)}</div>`;
    }).join('');
}
