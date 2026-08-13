// review 面板：进度 + Issue 卡（筛选/定位/逐项采用/忽略）+ anchors 高亮

import { escapeHtml } from '../security.js';
import { toast_, openArticle, currentAid } from '../app.js';
import { reviewApi, readReviewStream } from './api.js';
import { openLauncher } from './launcher.js';
import { openExport } from './export.js';

const SEV_LABEL = { error: '错误', warning: '警告', suggestion: '建议' };
const SEV_CLASS = { error: 'e', warning: 'w', suggestion: 's' };

// 运行一次完整检查：启动器 → 创建 → 流式收集 issue → 渲染面板
export async function runReview(aid) {
  const sel = await openLauncher();
  if (!sel) return;
  const busy = document.createElement('div');
  busy.className = 'ai-card';
  busy.innerHTML = '<div class="ai-head">成稿检查</div><div class="opt">正在准备快照…</div>';
  document.getElementById('cardflow').prepend(busy);
  try {
    const created = await reviewApi('/api/reviews', {
      method: 'POST',
      body: JSON.stringify({ article_id: aid, profile_selection: sel }),
    });
    const issues = await readStreamIssues(created.review_id);
    busy.remove();
    renderPanel(created.review_id, aid, issues, created.profile, sel);
  } catch (e) {
    busy.innerHTML = '<div class="ai-head">成稿检查失败</div><div class="opt">' + escapeHtml(e.message) + '</div>';
  }
}

async function readStreamIssues(reviewId) {
  const issues = [];
  await readReviewStream(`/api/reviews/${reviewId}/stream`, ev => {
    if (ev.type === 'issue') issues.push(ev.issue);
  });
  return issues;
}

function renderPanel(reviewId, aid, issues, profile, selection) {
  setAnchorMap(issues); // 供锚点定位回查
  document.querySelectorAll('.ai-card.review-panel').forEach(c => c.remove());
  const card = document.createElement('div');
  card.className = 'ai-card review-panel';
  const open = issues.filter(i => i.state === 'open');
  card.innerHTML = `
    <div class="ai-head">成稿检查 <span class="cnt">${open.length}</span>
      <button class="mini2" id="rv-recheck" style="float:right">复检</button></div>
    <div class="rv-filter">
      <button class="mini2 f on" data-f="all">全部</button>
      <button class="mini2 f" data-f="error">错误</button>
      <button class="mini2 f" data-f="warning">警告</button>
      <button class="mini2 f" data-f="suggestion">建议</button>
    </div>
    <div class="rv-list"></div>
    <div class="rv-foot"><button class="btn btn-p" id="rv-export">导出双版本</button></div>`;
  const flow = document.getElementById('cardflow');
  flow.prepend(card);

  const list = card.querySelector('.rv-list');
  const render = filter => {
    list.innerHTML = open.filter(i => filter === 'all' || i.severity === filter).map(i => `
      <div class="rv-item" data-iid="${i.id}" data-sev="${i.severity}">
        <div class="rv-top">
          <span class="rv-sev ${SEV_CLASS[i.severity]}">${SEV_LABEL[i.severity]}</span>
          <span class="rv-rule">${escapeHtml(i.rule_id)}</span>
          <span class="rv-scope">${i.anchor && i.anchor.block_id ? '定位' : '全文'}</span>
        </div>
        <div class="rv-reason">${escapeHtml(i.reason || '')}</div>
        <div class="rv-acts">
          <button class="mini2" data-x="accept">采用</button>
          <button class="mini2" data-x="ignore">忽略</button>
        </div>
      </div>`).join('') || '<div class="opt">没有未处理的问题 🎉</div>';
    card.querySelectorAll('.rv-item').forEach(item => {
      const iid = +item.dataset.iid;
      item.addEventListener('click', e => {
        if (e.target.closest('[data-x]')) return;
        highlightAnchor(item.dataset.iid);
      });
      item.querySelector('[data-x="accept"]').onclick = async e => {
        e.stopPropagation();
        try {
          const r = await reviewApi(`/api/reviews/${reviewId}/issues/${iid}/accept`, { method: 'POST' });
          toast_('已采用' + (r.action === 'master' ? '（主稿已更新）' : '（渠道补丁已创建）'));
          if (r.action === 'master') {
            setTimeout(() => openArticle(currentAid), 300); // 刷新正文显示修复
          }
          item.remove();
          updateCount();
        } catch (err) { toast_('采用失败：' + err.message); }
      };
      item.querySelector('[data-x="ignore"]').onclick = async e => {
        e.stopPropagation();
        await reviewApi(`/api/reviews/${reviewId}/issues/${iid}/ignore`, { method: 'POST' });
        item.remove();
        updateCount();
      };
    });
  };
  const updateCount = () => {
    const left = list.querySelectorAll('.rv-item').length;
    card.querySelector('.ai-head .cnt').textContent = left;
    if (!left) list.innerHTML = '<div class="opt">没有未处理的问题</div>';
  };
  card.querySelectorAll('.rv-filter .f').forEach(b => b.onclick = () => {
    card.querySelectorAll('.rv-filter .f').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    render(b.dataset.f);
  });
  card.querySelector('#rv-recheck').onclick = async () => {
    await reviewApi(`/api/reviews/${reviewId}/recheck`, { method: 'POST' });
    toast_('已创建新检查');
    renderPanel(undefined, aid, [], {});
    runReview(aid); // 重新走启动器（保留用户选择语义简化：重新选）
  };
  const exBtn = card.querySelector('#rv-export');
  if (exBtn) exBtn.onclick = () => {
    const channel = (selection && selection.channel && selection.channel.length) ? selection.channel[0] : null;
    openExport(reviewId, channel);
  };
  render('all');
}

// 锚点定位：滚动 + 高亮对应 Block
function highlightAnchor(iid) {
  const item = document.querySelector(`.rv-item[data-iid="${iid}"]`);
  if (!item) return;
  // 从当前面板的 issue 数据找锚点：简化用 data 属性回查（面板渲染时存 map）
  const issue = _anchorMap.get(+iid);
  if (!issue || !issue.anchor || !issue.anchor.block_id) { toast_('该问题无精确位置'); return; }
  const block = document.querySelector(`#article .blk.edit[data-bid="${issue.anchor.block_id}"]`);
  if (!block) { toast_('对应段落已不存在（可能已修改）'); return; }
  block.scrollIntoView({ behavior: 'smooth', block: 'center' });
  block.classList.add('rv-flash');
  setTimeout(() => block.classList.remove('rv-flash'), 1600);
}

// 供 highlightAnchor 回查的 issue 锚点表（renderPanel 时填充）
const _anchorMap = new Map();
export function setAnchorMap(issues) {
  _anchorMap.clear();
  issues.forEach(i => _anchorMap.set(i.id, i));
}
