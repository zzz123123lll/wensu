// review 规则管理：包浏览、规则启停/严重度覆盖、恢复默认、导入（两阶段）

import { escapeHtml } from '../security.js';
import { toast_ } from '../app.js';
import { reviewApi } from './api.js';

const SEV = ['error', 'warning', 'suggestion'];

export async function loadRulesSection() {
  const box = document.getElementById('rules-list');
  if (!box) return;
  box.innerHTML = '<div class="opt" style="font-size:12px">加载中…</div>';
  try {
    const r = await reviewApi('/api/review/packs');
    const html = r.packs.map(p =>
      `<div class="pf-item"><span class="k">${escapeHtml(p.name)}</span>
        <span class="m">v${escapeHtml(String(p.pack_version))} · ${p.rule_count} 条 · ${p.builtin ? '内置' : '自定义'}</span></div>`
    ).join('');
    box.innerHTML = html || '<div style="font-size:12px;color:var(--fg-3)">无规则包</div>';
  } catch (e) {
    box.innerHTML = '<div style="font-size:12px;color:var(--fg-3)">规则加载失败</div>';
  }
}

export async function openRuleImport() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,.wensu-rules';
  input.onchange = async () => {
    const f = input.files && input.files[0];
    if (!f) return;
    if (f.size > 200 * 1024) { toast_('文件超过 200KB 上限'); return; }
    const content = await f.text();
    try {
      const preview = await reviewApi('/api/review/rules/import', {
        method: 'POST',
        body: JSON.stringify({ content }),
      });
      const p = preview.preview;
      const detail = [
        `新增 ${p.added.length} 条`, `更新 ${p.changed.length} 条`, `拒绝 ${p.rejected.length} 条`,
      ].join(' · ');
      const ok = confirm(`导入预览：${detail}\n\n新增：${p.added.map(x => x.id).join(', ') || '无'}\n更新：${p.changed.map(x => x.id).join(', ') || '无'}${p.rejected.length ? `\n\n已拒绝（不会安装）：\n${p.rejected.map(x => x.id + ' → ' + x.reason).join('\n')}` : ''}\n\n确认安装？`);
      if (!ok) return;
      await reviewApi('/api/review/rules/import/confirm', { method: 'POST', body: JSON.stringify({ confirm_token: preview.token }) });
      toast_('规则已安装');
      loadRulesSection();
    } catch (e) {
      toast_('导入失败：' + e.message);
    }
  };
  input.click();
}
