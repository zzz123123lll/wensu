// review 导出：双预览（通用/渠道）+ 下载 + 摘要 manifest

import { escapeHtml } from '../security.js';
import { toast_ } from '../app.js';
import { reviewApi } from './api.js';

export async function openExport(reviewId, target) {
  const busy = document.createElement('div');
  busy.className = 'ai-card';
  busy.innerHTML = '<div class="ai-head">导出中</div><div class="opt">正在生成双版本 Markdown…</div>';
  document.getElementById('cardflow').prepend(busy);
  try {
    const r = await reviewApi(`/api/reviews/${reviewId}/exports`, {
      method: 'POST',
      body: JSON.stringify({ target: target || null }),
    });
    busy.remove();
    renderExportCard(r);
  } catch (e) {
    busy.innerHTML = '<div class="ai-head">导出失败</div><div class="opt">' + escapeHtml(e.message) + '</div>';
  }
}

function renderExportCard(r) {
  document.querySelectorAll('.ai-card.export-card').forEach(c => c.remove());
  const card = document.createElement('div');
  card.className = 'ai-card export-card';
  const stale = r.stale || [];
  card.innerHTML = `
    <div class="ai-head">导出 <span class="cnt">${escapeHtml(r.general_file)}</span>
      <button class="mini2" id="ex-close" style="float:right">关闭</button></div>
    ${stale.length ? `<div class="opt ex-warn">⚠ ${stale.length} 个渠道补丁已失效（原文已变化），渠道版未应用它们。请复检后重新导出。</div>` : ''}
    <div class="ex-tabs">
      <button class="mini2 on" data-tab="general">通用版</button>
      ${r.channel_file ? `<button class="mini2" data-tab="channel">渠道版</button>` : ''}
      <button class="mini2" data-tab="report">摘要</button>
    </div>
    <div class="ex-body" id="ex-body"><pre></pre></div>
    <div class="ex-acts">
      <button class="btn btn-p" id="ex-dl-general">下载通用版</button>
      ${r.channel_file ? '<button class="btn btn-p" id="ex-dl-channel">下载渠道版</button>' : ''}
      <button class="mini2" id="ex-dl-report">摘要</button>
    </div>`;
  document.getElementById('cardflow').prepend(card);

  const pre = card.querySelector('#ex-body pre');
  const tabs = card.querySelectorAll('.ex-tabs .mini2');
  const show = async (kind) => {
    tabs.forEach(t => t.classList.toggle('on', t.dataset.tab === kind));
    try {
      const resp = await fetch(`/api/review-exports/${r.export_id}/${kind}`);
      if (kind === 'report') {
        pre.textContent = JSON.stringify(await resp.json(), null, 2);
      } else {
        pre.textContent = await resp.text();
      }
    } catch (e) { pre.textContent = '预览失败：' + e.message; }
  };
  tabs.forEach(t => t.onclick = () => show(t.dataset.tab));
  card.querySelector('#ex-close').onclick = () => card.remove();
  card.querySelector('#ex-dl-general').onclick = () => { location.href = `/api/review-exports/${r.export_id}/general`; };
  const dlCh = card.querySelector('#ex-dl-channel');
  if (dlCh) dlCh.onclick = () => { location.href = `/api/review-exports/${r.export_id}/channel`; };
  card.querySelector('#ex-dl-report').onclick = () => { location.href = `/api/review-exports/${r.export_id}/report`; };
  show('general');
  toast_('导出完成');
}
