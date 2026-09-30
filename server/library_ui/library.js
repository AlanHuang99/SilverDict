'use strict';
const $ = s => document.querySelector(s);
let catalog = [], jobs = [], selected = new Set(), searchVersion = 0, refreshing = false;
const frames = new Set();
function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
async function api(path, options={}) {
  const response=await fetch('/api/library'+path, {...options,headers:{'Content-Type':'application/json','X-SilverDict-Library':'1',...options.headers}});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
let noticeTimer;
function notice(text) { $('#notice').textContent=text;$('#notice').hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('#notice').hidden=true,9000); }
function view(name) { for(const v of document.querySelectorAll('.view'))v.hidden=v.id!==name+'-view';for(const b of document.querySelectorAll('.nav'))b.classList.toggle('active',b.dataset.view===name);if(name==='jobs')refreshJobs(); }
for(const b of document.querySelectorAll('.nav'))b.onclick=()=>view(b.dataset.view);
$('#open-collection').onclick=()=>view('catalog');
function size(bytes) { return bytes>=1073741824?(bytes/1073741824).toFixed(1)+' GB':bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':Math.round(bytes/1024)+' KB'; }
function empty(title,text) { const box=node('div',undefined,'empty');box.append(node('h2',title),node('p',text));return box; }
function renderCatalog() {
  const filter=$('#filter').value.toLocaleLowerCase();const box=$('#catalog');box.replaceChildren();
  const visible=catalog.filter(x=>[x.title,x.path,x.format].join(' ').toLocaleLowerCase().includes(filter));
  for(const item of visible) {
    const card=node('article',undefined,'dictionary');
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selected.has(item.id);checkbox.setAttribute('aria-label','Select '+item.title);
    checkbox.disabled=['unsupported','ready','imported','queued','running'].includes(item.status);
    checkbox.onchange=()=>{checkbox.checked?selected.add(item.id):selected.delete(item.id);$('#selection-count').textContent=selected.size?`(${selected.size})`:'';};
    const body=node('div');body.append(node('h2',item.title),node('div',item.path,'path'));
    const actions=node('div',undefined,'actions');actions.append(node('span',item.format,'badge'),node('span',item.status,'badge '+item.status),node('span',size(item.bytes||0),'size'));
    if(item.status!=='unsupported') {
      if(!['ready','imported','queued','running'].includes(item.status)) {const b=node('button','Import');b.onclick=()=>enqueue(item.id,'import',b);actions.append(b);}
      const exportButton=node('button','Export StarDict','secondary');exportButton.onclick=()=>enqueue(item.id,'export',exportButton);actions.append(exportButton);
    }
    body.append(actions);if(item.error)body.append(node('p',item.error,'hint'));card.append(checkbox,body);box.append(card);
  }
  if(!visible.length)box.append(empty('No dictionaries found',filter?'Try a different filter.':'Add dictionary files to the configured collection directory, then refresh.'));
  const ready=catalog.filter(x=>['ready','imported'].includes(x.status)||x.dictionary_id).length;
  $('#catalog-summary').textContent=`${catalog.length} items · ${ready} available to read`;
}
async function refreshCatalog() {
  if(refreshing)return;refreshing=true;
  try { const data=await api('/catalog');catalog=data.items||[];const groups=data.groups||['Default Group'];const previous=$('#group').value;$('#group').replaceChildren();$('#group-options').replaceChildren();
    for(const group of groups){const name=typeof group==='string'?group:group.name;const option=node('option',name);option.value=name;$('#group').append(option);const o=node('option');o.value=name;$('#group-options').append(o);}
    if([...$('#group').options].some(o=>o.value===previous))$('#group').value=previous;
    renderCatalog();
  }catch(e){notice(e.message);}finally{refreshing=false;}
}
async function enqueue(source_id,action,button) {
  if(button)button.disabled=true;
  try {const group=$('#import-group').value.trim()||'Default Group';if(![...$('#group').options].some(o=>o.value===group))await api('/groups',{method:'POST',body:JSON.stringify({name:group})});await api('/jobs',{method:'POST',body:JSON.stringify({source_id,action,group})});selected.delete(source_id);$('#selection-count').textContent=selected.size?`(${selected.size})`:'';await refreshJobs();await refreshCatalog();}
  catch(e){notice(e.message);}finally{if(button)button.disabled=false;}
}
$('#import-selected').onclick=async()=>{const button=$('#import-selected');button.disabled=true;for(const id of [...selected])await enqueue(id,'import');button.disabled=false;view('jobs');};
$('#refresh').onclick=refreshCatalog;$('#filter').oninput=renderCatalog;
function renderJobs(){const box=$('#jobs');box.replaceChildren();const active=jobs.filter(j=>['queued','running'].includes(j.status)).length;$('#job-count').textContent=active?`(${active})`:'';
  for(const job of jobs){const card=node('article',undefined,'job');const heading=node('header');const item=catalog.find(i=>i.id===job.source_id);heading.append(node('h2',`${job.action==='export'?'Export':'Import'} · ${item?.title||job.source_id}`),node('span',job.status,'badge '+job.status));card.append(heading);card.append(node('p',job.message||job.status));
    if(['queued','running'].includes(job.status)){const progress=document.createElement('progress');progress.max=100;if(job.progress>0)progress.value=job.progress;progress.setAttribute('aria-label',job.status);card.append(progress);}
    if(job.download_url){const url=new URL(job.download_url,location.origin);if(url.origin===location.origin&&url.pathname.startsWith('/api/library/')){const a=node('a','Download StarDict package ↓');a.href=url.href;card.append(a);}}
    if(['failed','interrupted'].includes(job.status)){const b=node('button','Retry','secondary');b.onclick=()=>enqueue(job.source_id,job.action,b);card.append(b);}
    box.append(card);
  }
  if(!jobs.length)box.append(empty('Nothing in the queue','Imports and open-format exports will appear here.'));
}
async function refreshJobs(){try{const old=jobs.filter(j=>['queued','running'].includes(j.status)).length;jobs=(await api('/jobs')).jobs||[];renderJobs();if(old!==jobs.filter(j=>['queued','running'].includes(j.status)).length)await refreshCatalog();}catch(e){notice(e.message);}}
async function search(word,push=true){word=word.trim();if(!word)return;$('#query').value=word;view('search');const version=++searchVersion;$('#result-summary').textContent='Looking through your dictionaries…';$('#suggestions').replaceChildren();
  try{const group=$('#group').value||'Default Group';const data=await api('/search?'+new URLSearchParams({q:word,group}));if(version!==searchVersion)return;const articles=data.articles||[];const results=$('#results');results.replaceChildren();frames.clear();
    for(const article of articles){const card=node('article',undefined,'article');card.append(node('h2',article.title));const frame=document.createElement('iframe');frame.title=article.title;frame.sandbox='allow-scripts';frame.src='/library-frame';frame.referrerPolicy='no-referrer';frame.onload=()=>frame.contentWindow.postMessage({type:'article',html:article.html,id:article.id},'*');frames.add(frame);card.append(frame);results.append(card);}
    $('#result-summary').textContent=`${articles.length} ${articles.length===1?'dictionary':'dictionaries'} for “${word}”`;
    if(!articles.length)results.append(empty('No entry found','Try a suggested word, another spelling, or a different dictionary group.'));
    for(const suggestion of data.suggestions||[]){const b=node('button',suggestion);b.onclick=()=>search(suggestion);$('#suggestions').append(b);}
    if(push)history.pushState({},'', '/?'+new URLSearchParams({q:word,group}));
  }catch(e){if(version===searchVersion){$('#result-summary').textContent='Lookup could not complete';notice(e.message);}}
}
window.addEventListener('message',event=>{const frame=[...frames].find(f=>f.contentWindow===event.source);if(!frame||event.origin!=='null'||event.data?.silverdict!==true)return;const {type,value}=event.data;if(type==='height'&&Number.isFinite(value))frame.style.height=Math.max(160,Math.min(3000,value+25))+'px';if(type==='lookup'&&typeof value==='string'&&value.length<=200)search(value);});
$('#search-form').onsubmit=e=>{e.preventDefault();search($('#query').value);};
window.onpopstate=()=>{const p=new URLSearchParams(location.search);if(p.get('group'))$('#group').value=p.get('group');if(p.get('q'))search(p.get('q'),false);};
(async()=>{await refreshCatalog();await refreshJobs();const p=new URLSearchParams(location.search);if(p.get('group'))$('#group').value=p.get('group');if(p.get('q'))search(p.get('q'),false);setInterval(()=>{if(!document.hidden)refreshJobs();},4000);})();
