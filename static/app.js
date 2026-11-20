'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = {jobs: [], selected: '', pending: false, detailShown: new Set(), fingerprints: {}, filter: '',
  view: 'search', downloads: {}, dismissed: '', announced: new Set(), fileCount: 0};
const statusNames = {queued:'排队中',running:'处理中',waiting_captcha:'等待验证码',done:'已完成',failed:'未完成',cancelled:'已取消'};
const kindNames = {search:'搜索',download:'下载',detail:'详情'};
const kindIcons = {search:'search',download:'down',detail:'file'};
const viewTitles = {search:'字幕搜索',tasks:'任务队列',files:'已下载'};
const terminal = s => ['done','failed','cancelled'].includes(s);
const SVG = 'http://www.w3.org/2000/svg';

function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function icon(name) { const svg = document.createElementNS(SVG,'svg'); svg.setAttribute('class','icon'); svg.setAttribute('aria-hidden','true'); const use = document.createElementNS(SVG,'use'); use.setAttribute('href','#i-'+name); svg.append(use); return svg; }
function btn(text, cls, iconName) { const b = el('button', undefined, 'btn ' + cls); b.type = 'button'; if (iconName) b.append(icon(iconName)); b.append(document.createTextNode(text)); return b; }

/* ---------- snackbar ---------- */
let noticeTimer;
function notice(message, action) {
  const box = $('#notice'); clearTimeout(noticeTimer);
  box.replaceChildren(); box.hidden = !message;
  if (!message) return;
  box.append(el('span', message));
  if (action) { const b = el('button', action.label); b.type = 'button'; b.onclick = () => { box.hidden = true; action.run(); }; box.append(b); }
  noticeTimer = setTimeout(() => { box.hidden = true; }, 6000);
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  if (!response.ok) { let body; try { body = await response.json(); } catch { body = {detail:response.statusText}; } throw new Error(typeof body.detail === 'string' ? body.detail : '请求参数无效，请检查输入'); }
  return response.json();
}
function fileURL(path) { return '/api/files/' + path.split('/').map(encodeURIComponent).join('/'); }
function radioValue(name) { return document.querySelector(`input[name="${name}"]:checked`)?.value || ''; }
function setRadio(name, value) { const input = $$(`input[name="${name}"]`).find(n => n.value === (value || '')); if (input) input.checked = true; }
function formatBadge(fmt) { return String(fmt || '').toUpperCase().match(/[A-Z]{2,4}/)?.[0] || 'SUB'; }
function sizeText(n) { return n < 1024 ? n + ' B' : n < 1048576 ? (n/1024).toFixed(1) + ' KB' : (n/1048576).toFixed(1) + ' MB'; }
function timeText(seconds) {
  const diff = Date.now()/1000 - seconds;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff/60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff/3600) + ' 小时前';
  return new Date(seconds*1000).toLocaleDateString('zh-CN');
}

/* ---------- theme & views ---------- */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('#theme-toggle use').setAttribute('href', theme === 'dark' ? '#i-sun' : '#i-moon');
}
function initTheme() {
  let saved = null; try { saved = localStorage.getItem('subdog-theme'); } catch {}
  applyTheme(saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  $('#theme-toggle').onclick = () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next); try { localStorage.setItem('subdog-theme', next); } catch {}
  };
}
function showView(view, fromHash) {
  if (!viewTitles[view]) view = 'search';
  state.view = view;
  if (!fromHash && location.hash.slice(1) !== view) history.pushState(null, '', view === 'search' ? location.pathname : '#' + view);
  for (const v of Object.keys(viewTitles)) $('#view-' + v).hidden = v !== view;
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === view));
  $('#page-title').textContent = viewTitles[view];
  if (view === 'files') refreshFiles().catch(e => notice(e.message));
  scrollTo({top: 0});
}

/* ---------- results ---------- */
// Download jobs are titled after the subtitle; fall back to the title after a page reload.
function downloadsFor(item) {
  return state.jobs.filter(j => j.kind === 'download' &&
    (state.downloads[j.id] ? state.downloads[j.id] === item.id : j.title === item.title));
}
function updateResults() {
  const searches = state.jobs.filter(j => j.kind === 'search');
  if (!searches.some(j => j.id === state.selected)) state.selected = searches[0]?.id || '';
  const history = $('#search-history'); history.replaceChildren();
  for (const j of searches) { const o = el('option', j.title + ' · ' + statusNames[j.status]); o.value = j.id; history.append(o); }
  if (!searches.length) { const o = el('option','暂无搜索'); o.value = ''; history.append(o); }
  history.value = state.selected;
  const job = searches.find(j => j.id === state.selected), box = $('#results');
  const dl = state.jobs.filter(j => j.kind === 'download').map(j => [j.id, j.status, j.files, j.error]);
  const fingerprint = JSON.stringify([job?.id, job?.status, job?.results, job?.errors, job?.error, dl]);
  if (state.fingerprints.results === fingerprint) return;
  state.fingerprints.results = fingerprint;
  const errors = $('#source-errors'); errors.replaceChildren();
  for (const [source, message] of Object.entries(job?.errors || {})) {
    const w = el('div', undefined, 'alert'); w.append(el('b', source), document.createTextNode(message)); errors.append(w);
  }
  if (!job) { $('#result-count').textContent = 0; return; }
  box.replaceChildren(); box.className = 'grid';
  if (!job.results.length) {
    $('#result-count').textContent = 0;
    if (!terminal(job.status)) { for (let i = 0; i < 6; i++) box.append(el('div', undefined, 'skeleton')); return; }
    box.classList.add('empty');
    const empty = el('div', undefined, 'empty-state'), ic = el('span', undefined, 'empty-icon'); ic.append(icon('search'));
    empty.append(ic, el('h3', job.status === 'done' ? '未找到匹配字幕' : '搜索' + statusNames[job.status]),
      el('p', job.error || '试试英文原名、去掉年份，或放宽格式和语言。'));
    box.append(empty); return;
  }
  for (const item of job.results) box.append(resultCard(job, item));
  applyFilter();
}
function resultCard(job, item) {
  const badge = formatBadge(item.fmt);
  const card = el('article', undefined, 'card sub-card');
  card.dataset.search = [item.title, item.fmt, item.language, item.source_label].join(' ').toLowerCase();
  const cover = el('div', undefined, 'sub-cover cv-' + badge.toLowerCase());
  cover.append(el('span', item.source_label || item.source, 'src'), el('span', badge, 'fmt'));
  if (item.downloads) { const d = el('span', undefined, 'dl'); d.append(icon('down'), document.createTextNode(Number(item.downloads).toLocaleString('zh-CN'))); cover.append(d); }
  const body = el('div', undefined, 'sub-body'), tags = el('div', undefined, 'tags');
  if (item.language) tags.append(el('span', item.language, 'tag t-lang'));
  if (item.fmt && item.fmt.toUpperCase() !== badge) tags.append(el('span', item.fmt, 'tag'));
  if (item.uploaded) tags.append(el('span', String(item.uploaded).slice(0, 10), 'tag'));
  body.append(el('h3', item.title, 'sub-title'), tags);
  card.title = item.title;
  const actions = el('div', undefined, 'sub-actions');
  const detail = btn('查看详情', 'btn-text btn-sm');
  detail.onclick = () => startItem('/api/detail', job.id, item.id, detail);
  const jobs = downloadsFor(item), busy = jobs.some(j => !terminal(j.status));
  const download = btn(busy ? '下载中' : '下载', 'btn-primary btn-sm', 'down');
  download.disabled = busy;
  download.onclick = () => startItem('/api/download', job.id, item.id, download);
  actions.append(detail, download);
  card.append(cover, body, actions);
  const extra = el('div', undefined, 'sub-state');
  for (const d of jobs) {
    if (!terminal(d.status)) {
      extra.append(el('div', undefined, 'progress'));
      if (d.status === 'waiting_captcha') extra.append(el('div', '等待输入验证码…', 'state-line'));
    } else if (d.status === 'failed') extra.append(el('div', '下载未完成：' + (d.error || '请查看任务日志'), 'state-line failed'));
    for (const path of d.files) {
      const a = el('a', undefined, 'file-link'); a.href = fileURL(path);
      a.append(icon('check'), document.createTextNode(path.split('/').pop())); extra.append(a);
    }
  }
  if (extra.children.length) card.append(extra);
  return card;
}
function applyFilter() {
  const words = state.filter.toLowerCase().split(/\s+/).filter(Boolean);
  const cards = $$('#results .sub-card'); let shown = 0;
  for (const c of cards) { const v = words.every(w => c.dataset.search.includes(w)); c.classList.toggle('result-hidden', !v); shown += v; }
  $('#result-count').textContent = words.length && cards.length ? `${shown} / ${cards.length}` : cards.length;
}
async function startItem(path, search_id, item_id, button) {
  button.disabled = true;
  try {
    const result = await api(path, {search_id, item_id});
    if (path === '/api/download') { state.downloads[result.id] = item_id; notice('已加入下载队列', {label:'查看任务', run:() => showView('tasks')}); }
    else notice('');
    await poll();
  } catch (e) { notice(e.message); } finally { button.disabled = false; }
}

/* ---------- tasks ---------- */
function updateTasks() {
  const box = $('#tasks');
  const jobs = state.jobs.slice(0, 30);
  const active = state.jobs.filter(j => !terminal(j.status)).length;
  $('#task-summary').textContent = state.jobs.length ? `共 ${state.jobs.length} 个任务` + (active ? ` · ${active} 个进行中` : ' · 全部完成') : '';
  const badge = $('#nav-task-badge'); badge.hidden = !active; badge.textContent = active;
  const fp = JSON.stringify(jobs);
  if (state.fingerprints.tasks === fp) return;
  state.fingerprints.tasks = fp;
  box.replaceChildren();
  if (!jobs.length) {
    const empty = el('div', undefined, 'empty-state'), ic = el('span', undefined, 'empty-icon'); ic.append(icon('tasks'));
    empty.append(ic, el('h3', '还没有任务'), el('p', '搜索、查看详情和下载都会在这里排队处理。'));
    box.append(empty); return;
  }
  for (const job of jobs) {
    const card = el('div', undefined, 'card task ' + job.status);
    const ic = el('span', undefined, 'task-icon'); ic.append(icon(job.status === 'waiting_captcha' ? 'key' : job.status === 'done' ? 'check' : kindIcons[job.kind] || 'file'));
    const sub = el('div', undefined, 'task-sub');
    sub.append(el('span', statusNames[job.status], 'status ' + job.status), el('span', kindNames[job.kind] || job.kind));
    if (job.created) sub.append(el('span', new Date(job.created).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})));
    const actions = el('div', undefined, 'task-actions');
    if (job.status === 'waiting_captcha') { const b = btn('输入验证码', 'btn-primary btn-sm', 'key'); b.onclick = () => { state.dismissed = ''; syncCaptcha(true); }; actions.append(b); }
    if (!terminal(job.status)) { const c = btn('取消', 'btn-error btn-sm'); c.onclick = () => api('/api/jobs/' + job.id + '/cancel', {}).then(poll).catch(e => notice(e.message)); actions.append(c); }
    const extra = el('div', undefined, 'task-extra');
    if (job.status === 'running' || job.status === 'queued') extra.append(el('div', undefined, 'progress'));
    for (const message of job.warnings || []) extra.append(el('div', message, 'alert'));
    if (job.error) extra.append(el('pre', job.error, 'task-error'));
    if (job.files.length) {
      const files = el('div', undefined, 'task-files');
      for (const path of job.files) { const a = el('a', undefined, 'file-link'); a.href = fileURL(path); a.append(icon('down'), document.createTextNode(path.split('/').pop())); files.append(a); }
      extra.append(files);
    }
    if (job.logs.length) { const d = el('details'); d.append(el('summary', `查看日志（${job.logs.length}）`), el('pre', job.logs.join('\n'), 'log')); extra.append(d); }
    card.append(ic, el('p', job.title, 'task-title'), actions, sub, extra);
    box.append(card);
  }
}
function announce() {
  for (const job of state.jobs) {
    if (!terminal(job.status) || state.announced.has(job.id)) continue;
    state.announced.add(job.id);
    if (job.kind === 'detail' && job.status === 'done' && job.detail && !state.detailShown.has(job.id)) { state.detailShown.add(job.id); showDetail(job.detail); }
    if (job.kind === 'download') {
      if (job.status === 'done') { notice('下载完成：' + (job.files[0]?.split('/').pop() || job.title), {label:'查看文件', run:() => showView('files')}); refreshFiles().catch(() => {}); }
      else if (job.status === 'failed') notice('下载未完成：' + (job.error || job.title));
    }
  }
}
function showDetail(data) {
  const content = $('#detail-content'); content.replaceChildren(el('h3', data.title));
  const fields = el('dl', undefined, 'detail-fields');
  for (const [key, label] of Object.entries({fmt:'字幕格式',language:'语言',rating:'评分',downloads:'下载次数',uploaded:'上传时间',file_size:'文件大小',maker:'制作',reviser:'校订'})) {
    if (data[key]) { const cell = el('div'); cell.append(el('dt', label), el('dd', String(data[key]))); fields.append(cell); }
  }
  content.append(fields);
  if (data.intro) content.append(el('h4','简介'), el('p', data.intro));
  if (data.related?.length) { content.append(el('h4','相关字幕')); const list = el('ul'); data.related.forEach(i => list.append(el('li', i.title))); content.append(list); }
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
}

/* ---------- captcha dialog ---------- */
function syncCaptcha(force) {
  const waiting = state.jobs.filter(j => j.status === 'waiting_captcha' && j.challenge);
  $('#captcha-alert').hidden = !waiting.length;
  const dialog = $('#captcha-dialog'), job = waiting[0];
  if (!job) { if (dialog.open) dialog.close(); dialog.dataset.challenge = ''; return; }
  if (dialog.open && dialog.dataset.challenge === job.challenge.id) return; // keep typed input across polls
  if (!force && state.dismissed === job.challenge.id) return;
  if ($('#detail-dialog').open) return;
  dialog.dataset.challenge = job.challenge.id; dialog.dataset.expires = job.challenge.expires_at;
  const form = el('form', undefined, 'captcha');
  const intro = el('p', undefined, 'captcha-for'); intro.append(document.createTextNode((kindNames[job.kind] || '任务') + '：'), el('b', job.title));
  const image = el('img'); image.src = job.challenge.image; image.alt = '5 位数字验证码';
  const label = el('label', '请输入图中的 5 位数字'); label.htmlFor = 'captcha-input';
  const input = el('input'); input.id = 'captcha-input'; input.inputMode = 'numeric'; input.pattern = '[0-9]{5}'; input.maxLength = 5; input.required = true; input.autocomplete = 'off'; input.setAttribute('aria-label','验证码');
  const buttons = el('div', undefined, 'buttons');
  const cancel = btn('取消任务', 'btn-text btn-sm'), refresh = btn('换一张', 'btn-outline btn-sm', 'refresh');
  const submit = el('button', '确认', 'btn btn-primary btn-sm'); submit.type = 'submit';
  async function send(action) {
    try { await api('/api/jobs/' + job.id + '/captcha', {challenge_id: job.challenge.id, action, answer: input.value}); dialog.dataset.challenge = ''; dialog.close(); await poll(); }
    catch (e) { notice(e.message); }
  }
  form.onsubmit = e => { e.preventDefault(); send('answer'); };
  refresh.onclick = () => send('refresh'); cancel.onclick = () => send('cancel');
  buttons.append(cancel, refresh, submit);
  form.append(intro, image, label, input, buttons);
  $('#captcha-body').replaceChildren(form);
  tickTimer();
  if (!dialog.open) dialog.showModal();
  input.focus();
}
function tickTimer() {
  const d = $('#captcha-dialog'); if (!d.open) return;
  const left = Math.max(0, Math.round(Number(d.dataset.expires) - Date.now()/1000));
  $('#captcha-timer').textContent = `${Math.floor(left/60)}:${String(left%60).padStart(2,'0')}`;
}
$('#captcha-dialog').addEventListener('cancel', () => { state.dismissed = $('#captcha-dialog').dataset.challenge; $('#captcha-dialog').dataset.challenge = ''; });
$('#captcha-alert').onclick = () => { state.dismissed = ''; syncCaptcha(true); };

/* ---------- files ---------- */
async function refreshFiles() {
  const files = await api('/api/files'), box = $('#files');
  $('#file-count').textContent = files.length;
  const badge = $('#nav-file-badge'); badge.hidden = !files.length; badge.textContent = files.length;
  const fp = JSON.stringify(files);
  if (state.fingerprints.files === fp) return;
  state.fingerprints.files = fp; box.replaceChildren();
  if (!files.length) { box.append(el('div', '还没有下载文件。', 'table-empty')); return; }
  const head = el('div', undefined, 'trow thead');
  head.append(el('span'), el('span', '文件名'), el('span', '大小'), el('span', '时间'), el('span'));
  box.append(head);
  for (const file of files) {
    const ext = (file.name.match(/\.([a-z0-9]{1,4})$/i)?.[1] || '?').toUpperCase();
    const row = el('a', undefined, 'trow'); row.href = fileURL(file.path); row.title = file.path;
    const name = el('span', file.name, 'fname'); name.append(el('small', sizeText(file.size) + ' · ' + timeText(file.modified)));
    row.append(el('span', ext, 'ext' + (['ZIP','RAR','7Z'].includes(ext) ? ' archive' : '')), name,
      el('span', sizeText(file.size), 'tcell'), el('span', timeText(file.modified), 'tcell'), icon('down'));
    box.append(row);
  }
}

/* ---------- polling & wiring ---------- */
async function poll() {
  if (state.pending) return; state.pending = true;
  const conn = $('#connection');
  try {
    state.jobs = await api('/api/jobs'); conn.textContent = '服务已连接'; conn.className = 'conn ok';
    updateResults(); updateTasks(); syncCaptcha(false); announce();
  } catch (e) { conn.textContent = '连接中断'; conn.className = 'conn down'; notice(e.message); }
  finally { state.pending = false; }
}
$('#search-form').onsubmit = async e => {
  e.preventDefault();
  const sources = $$('input[name=sources]:checked').map(n => n.value);
  if (!sources.length) { notice('请至少选择一个字幕来源'); return; }
  const button = $('#search-button'); button.disabled = true;
  try {
    const result = await api('/api/search', {keyword:$('#keyword').value, sources, fmt:radioValue('fmt'), language:radioValue('language'), limit:Number($('#limit').value), use_ocr:$('#use-ocr').checked});
    state.selected = result.id; state.filter = ''; $('#result-filter').value = ''; notice(''); await poll();
  } catch (e) { notice(e.message); } finally { button.disabled = false; }
};
$$('.nav-item').forEach(n => n.onclick = () => showView(n.dataset.view));
$('#search-history').onchange = e => { state.selected = e.target.value; updateResults(); };
$('#result-filter').oninput = e => { state.filter = e.target.value; applyFilter(); };
$('#refresh-files').onclick = () => { state.fingerprints.files = ''; refreshFiles().catch(e => notice(e.message)); };
$('#close-detail').onclick = () => $('#detail-dialog').close();
$('#detail-dialog').onclick = e => { if (e.target === e.currentTarget) e.currentTarget.close(); };
addEventListener('popstate', () => showView(location.hash.slice(1) || 'search', true));
async function init() {
  initTheme();
  if (location.hash.length > 1) showView(location.hash.slice(1), true);
  try {
    const settings = await api('/api/settings');
    $('#keyword').value = settings.keyword; $('#limit').value = settings.limit;
    setRadio('fmt', settings.fmt); setRadio('language', settings.language);
    $('#use-ocr').checked = settings.use_ocr;
    $$('input[name=sources]').forEach(n => n.checked = settings.sources.includes(n.value));
    await refreshFiles();
  } catch (e) { notice(e.message); }
  // Jobs already finished before this page load are not announced again.
  try { (await api('/api/jobs')).forEach(j => { if (terminal(j.status)) { state.announced.add(j.id); state.detailShown.add(j.id); } }); } catch {}
  await poll();
  setInterval(poll, 1600); setInterval(tickTimer, 1000);
  setInterval(() => refreshFiles().catch(() => {}), 10000);
}
init();
