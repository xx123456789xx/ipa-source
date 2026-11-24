'use strict';
const $ = (s) => document.querySelector(s);
const sourceURL = new URL('source.json', window.location.href).href;
$('#source-url').value = sourceURL;
if (location.protocol === 'https:') {
  $('#lc-import').href = 'livecontainer://source?url=' + encodeURIComponent(sourceURL);
  $('#lc-import').hidden = false;
}
$('#copy').addEventListener('click', async () => {
  const field = $('#source-url');
  try {
    await navigator.clipboard.writeText(field.value);
    $('#message').textContent = '已复制。请在 LiveContainer / SideStore 支持的添加源页面粘贴此地址。';
  } catch (_) {
    field.focus(); field.select();
    $('#message').textContent = '请长按地址框，手动复制选中的订阅地址。';
  }
});
const safeHTTPS = (value) => {
  try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password ? u.href : ''; }
  catch (_) { return ''; }
};
// --- Pending-add success state -------------------------------------------
// add.js stores a pending submission in localStorage before jumping to
// GitHub. Poll source.json until the app actually appears: that, not the
// Issue submission, is the real "添加成功".
function pendingSubmission() {
  try {
    const pending = JSON.parse(window.localStorage.getItem('ipa-source-pending') || 'null');
    if (!pending || !pending.time || Date.now() - pending.time > 2 * 3600 * 1000) return null;
    return pending;
  } catch (_) { return null; }
}
function matchPending(source, pending) {
  for (const app of source.apps || []) {
    const urls = [app.downloadURL, ...((app.versions || []).map((v) => v.downloadURL))].filter(Boolean);
    if (pending.repo && urls.some((u) => u.startsWith('https://github.com/' + pending.repo + '/'))) return app;
    if (!pending.repo && urls.includes(pending.url)) return app;
  }
  return null;
}
function watchPending(initialSource) {
  const pending = pendingSubmission();
  const banner = $('#add-status');
  if (!pending || !banner) return;
  banner.hidden = false;
  const found = matchPending(initialSource, pending);
  if (found) return showAdded(found);
  banner.textContent = '⏳ 添加处理中：已提交到 GitHub，正在校验和发布。发布完成后这里会变成成功状态，也可以看 Issue 里的最终留言。';
  let tries = 0;
  const timer = setInterval(async () => {
    tries += 1;
    try {
      const response = await fetch('source.json', {cache: 'no-store'});
      if (response.ok) {
        const app = matchPending(await response.json(), pending);
        if (app) { clearInterval(timer); showAdded(app); return; }
      }
    } catch (_) { /* keep polling; transient network errors are normal here */ }
    if (tries >= 45) { // ~15 minutes at 20s intervals
      clearInterval(timer);
      banner.textContent = '⏳ 还没有在源里看到这个应用：可能仍在排队，也可能发布失败。去 GitHub 的 Issue / Actions 看最终状态最准。';
    }
  }, 20000);
  function showAdded(app) {
    try { window.localStorage.removeItem('ipa-source-pending'); } catch (_) {}
    banner.textContent = `✅ 添加成功：${app.name} v${app.version} 已发布到源里，去 LiveContainer 刷新源就能看到。`;
  }
}
// --- Store-style rendering -------------------------------------------------
function showEmpty() {
  const box = document.createElement('div'); box.className = 'empty';
  const symbol = document.createElement('div'); symbol.className = 'symbol'; symbol.textContent = '＋';
  const title = document.createElement('h3'); title.textContent = '你的应用收藏，从这里开始';
  const desc = document.createElement('p'); desc.textContent = '当前是空白模板。往 config.json 添加项目，部署后这里会自动显示应用。';
  box.append(symbol, title, desc); $('#apps').append(box);
}
function fmtSize(bytes) {
  const mb = Number(bytes || 0) / 1048576;
  return mb > 0 ? mb.toFixed(1) + ' MB' : '';
}
function fmtDate(value) {
  const d = new Date(value || '');
  return isNaN(d) ? '' : d.toLocaleDateString('zh-CN');
}
function buildCard(app) {
  const version = (app.versions && app.versions[0]) || app;
  const card = document.createElement('article'); card.className = 's-card';
  const tint = /^[0-9a-fA-F]{6}$/.test(app.tintColor || '') ? app.tintColor : '4263EB';
  card.style.setProperty('--tint', '#' + tint);
  card.dataset.search = [app.name, app.subtitle, app.bundleIdentifier, app.developerName]
    .filter(Boolean).join(' ').toLowerCase();

  const head = document.createElement('div'); head.className = 's-card-head';
  const icon = document.createElement('img');
  icon.className = 's-icon'; icon.alt = ''; icon.loading = 'lazy';
  icon.src = safeHTTPS(app.iconURL) || 'icon.png';
  icon.addEventListener('error', () => { icon.src = 'icon.png'; }, {once: true});
  const main = document.createElement('div'); main.className = 's-main';
  const name = document.createElement('h3'); name.className = 's-name'; name.textContent = app.name || '';
  const sub = document.createElement('p'); sub.className = 's-sub';
  sub.textContent = app.subtitle || app.developerName || '';
  const meta = document.createElement('p');
  meta.className = 's-meta';
  meta.textContent = ['v' + (version.version || app.version || ''),
    fmtSize(version.size || app.size),
    (version.minOSVersion || app.minOSVersion) ? 'iOS ' + (version.minOSVersion || app.minOSVersion) + '+' : '']
    .filter(Boolean).join(' · ');
  main.append(name, sub, meta);
  head.append(icon, main);
  const url = safeHTTPS(version.downloadURL || app.downloadURL);
  if (url) {
    const get = document.createElement('a');
    get.className = 's-get'; get.href = url;
    get.rel = 'noopener noreferrer'; get.target = '_blank';
    get.textContent = '获取';
    head.append(get);
  }
  card.append(head);

  const details = document.createElement('div');
  details.className = 's-details'; details.hidden = true;
  if (app.localizedDescription) {
    const desc = document.createElement('p'); desc.textContent = app.localizedDescription;
    details.append(desc);
  }
  const facts = document.createElement('p'); facts.className = 's-facts';
  facts.textContent = [
    app.developerName ? '开发者：' + app.developerName : '',
    app.bundleIdentifier ? '包名：' + app.bundleIdentifier : '',
    fmtDate(app.versionDate || version.date) ? '更新：' + fmtDate(app.versionDate || version.date) : '']
    .filter(Boolean).join('　');
  if (facts.textContent) details.append(facts);
  const notes = app.versionDescription || version.versionDescription;
  if (notes) {
    const box = document.createElement('div'); box.className = 's-notes'; box.textContent = notes;
    details.append(box);
  }
  if (url) {
    const dl = document.createElement('a');
    dl.className = 's-dl'; dl.href = url;
    dl.rel = 'noopener noreferrer'; dl.target = '_blank';
    dl.textContent = '下载 IPA ↗';
    details.append(dl);
  }
  const more = document.createElement('button');
  more.type = 'button'; more.className = 's-more'; more.textContent = '详情 ▾';
  more.addEventListener('click', () => {
    details.hidden = !details.hidden;
    more.textContent = details.hidden ? '详情 ▾' : '收起 ▴';
  });
  card.append(more, details);
  return card;
}
function wireSearch() {
  const input = $('#search');
  if (!input) return;
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    let visible = 0;
    for (const card of document.querySelectorAll('.s-card')) {
      const hit = !q || card.dataset.search.includes(q);
      card.hidden = !hit;
      if (hit) visible += 1;
    }
    const nomatch = $('#nomatch');
    if (nomatch) nomatch.hidden = visible > 0;
  });
}
let lastLoad = 0;
let searchWired = false;
let loadedSignature = '';
function sourceSignature(source) {
  return (source.apps || []).map((a) => `${a.bundleIdentifier}@${a.version}:${a.size}`).join('|');
}
async function fetchSource() {
  const response = await fetch('source.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('source load failed');
  return response.json();
}
function hideUpdateBar() { const bar = $('#update-bar'); if (bar) bar.hidden = true; }
function renderSource(source, first) {
  document.title = source.name;
  $('#title').textContent = source.name;
  $('#subtitle').textContent = source.subtitle || source.description || '';
  $('#count').textContent = `${source.apps.length} 个应用`;
  $('#apps').replaceChildren();
  if (!source.apps.length) showEmpty();
  for (const app of source.apps) $('#apps').append(buildCard(app));
  if (!searchWired) { wireSearch(); searchWired = true; }
  const search = $('#search');
  if (search && search.value) search.dispatchEvent(new Event('input'));
  $('#message').textContent = location.protocol === 'https:'
    ? '每 30 分钟计划检查上游；GitHub 调度可能延迟，客户端需刷新源。'
    : '当前为本地预览。部署到 GitHub Pages 后，才有可订阅的公网 HTTPS 地址。';
  loadedSignature = sourceSignature(source);
  lastLoad = Date.now();
  hideUpdateBar();
  if (first) watchPending(source);
}
async function loadSource(first) {
  try { renderSource(await fetchSource(), first); }
  catch (_) {
    $('#message').textContent = '源暂时无法读取，请检查 source.json 是否已生成并正确部署。';
  }
}
// No browser chrome exists in a home-screen web app, so the page keeps
// itself current: returning to the front (or a 90s tick while visible)
// re-checks the source; new content applies silently, unless the user
// is mid-search — then a tappable banner offers the refresh instead.
async function checkForUpdate() {
  if (document.hidden || !loadedSignature) return;
  try {
    const source = await fetchSource();
    if (sourceSignature(source) === loadedSignature) return;
    const search = $('#search');
    if (search && (search.value || document.activeElement === search)) {
      const bar = $('#update-bar');
      if (bar) bar.hidden = false;
    } else {
      renderSource(source, false);
    }
  } catch (_) { /* transient network errors are normal; next tick retries */ }
}
loadSource(true);
const updateBar = $('#update-bar');
if (updateBar) updateBar.addEventListener('click', () => loadSource(false));
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && Date.now() - lastLoad > 60 * 1000) checkForUpdate();
});
setInterval(checkForUpdate, 90 * 1000);
