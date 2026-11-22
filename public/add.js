'use strict';
const form=document.querySelector('#add-form');
const message=document.querySelector('#form-message');
// The source repository is derived from this page's own GitHub Pages
// address (<owner>.github.io/<repo>/...), never hardcoded: forks and
// renamed accounts keep working. The fallback only covers local previews.
function sourceRepo(){
  const host=window.location.hostname;
  const segments=window.location.pathname.split('/').filter(Boolean);
  if(host.endsWith('.github.io')&&segments.length>=1){
    return {owner:host.slice(0,-'.github.io'.length),repo:segments[0]};
  }
  return {owner:'xx123456789xx',repo:'ipa-source'};
}
form.addEventListener('submit',event=>{
  event.preventDefault();
  const raw=document.querySelector('#project-url').value.trim();
  const pattern=document.querySelector('#asset-pattern').value.trim()||'*.ipa';
  let url;
  try{url=new URL(raw);}catch(_){message.textContent='请输入有效的 HTTPS 网址。';return;}
  if(url.protocol!=='https:'||url.username||url.password){message.textContent='只接受不带账号密码的 HTTPS 链接。';return;}
  if(url.search||url.hash){message.textContent='请移除查询参数和锚点；不接受带临时令牌的私有下载链接。';return;}
  if(pattern.length>120||!pattern.toLowerCase().endsWith('.ipa')){message.textContent='文件匹配规则请以 .ipa 结尾，最多 120 个字符。';return;}
  const body=[
    'IPA_SOURCE_REQUEST_V1',
    `Source URL: ${url.href}`,
    `Asset pattern: ${pattern}`,
    `Include prereleases: ${document.querySelector('#prerelease').checked?'true':'false'}`
  ].join('\n');
  // Remember this submission on this device (same origin, survives the
  // GitHub round trip). The homepage watches for the app to appear in
  // source.json and turns that into a visible success state.
  try{
    let repoPath=null;
    if(url.hostname==='github.com'){
      const parts=url.pathname.split('/').filter(Boolean);
      if(parts.length>=2) repoPath=parts.slice(0,2).join('/');
    }
    window.localStorage.setItem('ipa-source-pending',JSON.stringify(
      {url:url.href,repo:repoPath,time:Date.now()}));
  }catch(_){/* storage unavailable: the Issue thread still reports status */}
  const query=new URLSearchParams({title:'[IPA Source] Add project',body});
  message.textContent='正在打开 GitHub 确认页；请核对公开请求内容并点击提交。提交后回到源首页，这里会显示添加是否成功。';
  const {owner,repo}=sourceRepo();
  window.location.assign(`https://github.com/${owner}/${repo}/issues/new?${query.toString()}`);
});
