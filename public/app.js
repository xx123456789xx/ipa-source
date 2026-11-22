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
function showEmpty() {
  const box = document.createElement('div'); box.className = 'empty';
  const symbol = document.createElement('div'); symbol.className = 'symbol'; symbol.textContent = '＋';
  const title = document.createElement('h3'); title.textContent = '你的应用收藏，从这里开始';
  const desc = document.createElement('p'); desc.textContent = '当前是空白模板。往 config.json 添加项目，部署后这里会自动显示应用。';
  box.append(symbol, title, desc); $('#apps').append(box);
}
(async () => {
  try {
    const response = await fetch('source.json', {cache: 'no-store'});
    if (!response.ok) throw new Error('source load failed');
    const source = await response.json();
    document.title = source.name;
    $('#title').textContent = source.name;
    $('#subtitle').textContent = source.subtitle || source.description || '';
    $('#count').textContent = `${source.apps.length} 个应用`;
    if (!source.apps.length) showEmpty();
    for (const app of source.apps) {
      const fragment = $('#card').content.cloneNode(true);
      const version = app.versions?.[0] || app;
      const icon = fragment.querySelector('img');
      icon.src = safeHTTPS(app.iconURL) || 'icon.png';
      icon.addEventListener('error', () => {icon.src = 'icon.png';}, {once: true});
      fragment.querySelector('h3').textContent = app.name;
      fragment.querySelector('.app-id').textContent = app.bundleIdentifier;
      const size = Number(version.size || app.size || 0) / 1048576;
      fragment.querySelector('.app-meta').textContent = `v${version.version} · ${size.toFixed(1)} MB`;
      const link = fragment.querySelector('.download');
      const url = safeHTTPS(version.downloadURL || app.downloadURL);
      if (url) link.href = url; else link.remove();
      $('#apps').append(fragment);
    }
    $('#message').textContent = location.protocol === 'https:'
      ? '每 30 分钟计划检查上游；GitHub 调度可能延迟，客户端需刷新源。'
      : '当前为本地预览。部署到 GitHub Pages 后，才有可订阅的公网 HTTPS 地址。';
    watchPending(source);
  } catch (_) {
    $('#message').textContent = '源暂时无法读取，请检查 source.json 是否已生成并正确部署。';
  }
})();
