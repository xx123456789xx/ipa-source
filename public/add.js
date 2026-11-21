'use strict';
const form=document.querySelector('#add-form');
const message=document.querySelector('#form-message');
const owner='xx123456789xx';
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
  const query=new URLSearchParams({title:'[IPA Source] Add project',body});
  message.textContent='正在打开 GitHub 确认页；请核对公开请求内容并点击提交。';
  window.location.assign(`https://github.com/${owner}/ipa-source/issues/new?${query.toString()}`);
});
