'use strict';
const $ = s => document.querySelector(s);
let catalog = [], jobs = [], selected = new Set(), searchVersion = 0, refreshing = false;
const frames = new Set();
let searchController, readingRows = [], readingGroup, settingsVersion = 0, settingsSaving = false;
function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
async function api(path, options={}) {
  const response=await fetch('/api/library'+path, {...options,headers:{'Content-Type':'application/json','X-SilverDict-Library':'1',...options.headers}});
  let data;
  try { data=JSON.parse(await response.text()); }
  catch { throw new Error(`The server returned an unreadable response (${response.status}). Please try again.`); }
  if(!response.ok)throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
let noticeTimer;
function notice(text) { $('#notice').textContent=text;$('#notice').hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('#notice').hidden=true,9000); }
function view(name) { for(const v of document.querySelectorAll('.view'))v.hidden=v.id!==name+'-view';for(const b of document.querySelectorAll('.nav'))b.classList.toggle('active',b.dataset.view===name);if(name==='jobs')refreshJobs();if(name==='settings')loadReadingSettings(); }
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
    checkbox.disabled=['unsupported','queued','running'].includes(item.status);
    checkbox.onchange=()=>{checkbox.checked?selected.add(item.id):selected.delete(item.id);$('#selection-count').textContent=selected.size?`(${selected.size})`:'';};
    const body=node('div');body.append(node('h2',item.title),node('div',item.path,'path'));
    const actions=node('div',undefined,'actions');actions.append(node('span',item.format,'badge'),node('span',item.status,'badge '+item.status),node('span',size(item.bytes||0),'size'));
    if(item.status!=='unsupported') {
      if(!['queued','running'].includes(item.status)) {const b=node('button',item.dictionary_id?'Add to group':'Import');b.onclick=()=>enqueue(item.id,'import',b);actions.append(b);}
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
    const previousSettings=$('#settings-group').value;$('#settings-group').replaceChildren(...[...$('#group').options].map(o=>o.cloneNode(true)));if([...$('#settings-group').options].some(o=>o.value===previousSettings))$('#settings-group').value=previousSettings;
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
function renderJobs(){
  const box=$('#jobs');box.replaceChildren();
  const active=jobs.filter(j=>['queued','running'].includes(j.status)).length;$('#job-count').textContent=active?`(${active})`:'';
  for(const job of jobs){
    const failed=['failed','interrupted'].includes(job.status);
    const retry=failed&&jobs.find(j=>j.source_id===job.source_id&&j.action===job.action&&j.status==='completed'&&Date.parse(j.created_at)>Date.parse(job.created_at));
    const card=node('article',undefined,'job');const heading=node('header');const item=catalog.find(i=>i.id===job.source_id);
    const action=job.action==='export'?'Export':'Import';
    heading.append(node('h2',`${action} · ${item?.title||job.source_id}`),node('span',retry?'Retry succeeded':job.status,'badge '+(retry?'completed':job.status)));card.append(heading);
    if(retry){
      card.append(node('p',`A later ${action.toLowerCase()} completed successfully. This earlier failure is kept for history.`));
      const details=node('details');details.append(node('summary','Earlier error'),node('p',job.message||job.status));card.append(details);
    }else card.append(node('p',job.message||job.status));
    if(['queued','running'].includes(job.status)){const progress=document.createElement('progress');progress.max=100;if(job.progress>0)progress.value=job.progress;progress.setAttribute('aria-label',job.status);card.append(progress);}
    if(job.download_url){const url=new URL(job.download_url,location.origin);if(url.origin===location.origin&&url.pathname.startsWith('/api/library/')){const a=node('a','Download StarDict package ↓');a.href=url.href;card.append(a);}}
    if(failed&&!retry){const b=node('button','Retry','secondary');b.onclick=()=>enqueue(job.source_id,job.action,b);card.append(b);}
    box.append(card);
  }
  if(!jobs.length)box.append(empty('Nothing in the queue','Imports and open-format exports will appear here.'));
}
async function refreshJobs(){try{const old=jobs.filter(j=>['queued','running'].includes(j.status)).length;const next=(await api('/jobs')).jobs||[];const changed=JSON.stringify(next)!==JSON.stringify(jobs);jobs=next;if(changed||!$('#jobs').children.length)renderJobs();if(old!==jobs.filter(j=>['queued','running'].includes(j.status)).length)await refreshCatalog();}catch(e){notice(e.message);}}
function entryCard(article, word, group, version, signal) {
  const card=node('details',undefined,'article');
  const summary=node('summary');summary.append(node('span',article.title));card.append(summary);
  const body=node('div',undefined,'entry-body');card.append(body);
  let html, loading=false, frame;
  function unmount() { if(frame){frames.delete(frame);frame.remove();frame=null;} }
  async function mount() {
    if(!card.open || loading || frame || signal.aborted)return;
    loading=true;body.replaceChildren(node('p','Loading entry…','entry-status'));
    try {
      if(html===undefined)html=(await api('/entry?'+new URLSearchParams({q:word,group,id:article.id}),{signal})).html;
      if(!card.open || version!==searchVersion || signal.aborted)return;
      frame=document.createElement('iframe');frame.title=article.title;frame.sandbox='allow-scripts';frame.src='/library-frame';frame.referrerPolicy='no-referrer';
      const mounted=frame;
      frame.onload=()=>mounted.contentWindow?.postMessage({type:'article',html,id:article.id},'*');
      frames.add(frame);body.replaceChildren(frame);
    } catch(e) {
      if(signal.aborted || version!==searchVersion)return;
      const message=node('p',e.message,'entry-status');const retry=node('button','Retry entry','secondary');retry.onclick=mount;body.replaceChildren(message,retry);
    } finally { loading=false; }
  }
  card.addEventListener('toggle',()=>card.open?mount():unmount());
  return card;
}
$('#expand-all').onclick=()=>{for(const card of document.querySelectorAll('#results details'))card.open=true;};
$('#collapse-all').onclick=()=>{for(const card of document.querySelectorAll('#results details'))card.open=false;};
async function search(word,push=true){
  word=word.trim();if(!word)return;$('#query').value=word;view('search');
  searchController?.abort();searchController=new AbortController();const {signal}=searchController;
  const version=++searchVersion;$('#result-summary').textContent='Looking through your dictionaries…';$('#suggestions').replaceChildren();
  const results=$('#results');results.replaceChildren();frames.clear();$('#entry-controls').hidden=true;
  try{
    const group=$('#group').value||'Default Group';
    const data=await api('/search?'+new URLSearchParams({q:word,group,deferred:'1'}),{signal});
    if(version!==searchVersion)return;
    const articles=data.articles||[];
    for(const article of articles)results.append(entryCard(article,word,group,version,signal));
    const first=results.querySelector('details');if(first)first.open=true;
    $('#entry-controls').hidden=!articles.length;
    const warnings=data.warnings||[];
    if(warnings.length){const warning=node('aside',undefined,'lookup-warning');warning.setAttribute('role','status');warning.append(node('strong','Some entries could not be read'));for(const item of warnings)warning.append(node('p',`${item.title}: ${item.error}`));results.prepend(warning);}
    $('#result-summary').textContent=`${articles.length} ${articles.length===1?'dictionary':'dictionaries'} for “${word}”`;
    if(!articles.length)results.append(empty('No entry found','Try another spelling, a different group, or enable more dictionaries in Settings.'));
    for(const suggestion of data.suggestions||[]){const b=node('button',suggestion);b.onclick=()=>search(suggestion);$('#suggestions').append(b);}
    if(push)history.pushState({},'', '/?'+new URLSearchParams({q:word,group}));
  }catch(e){if(version===searchVersion&&!signal.aborted){$('#result-summary').textContent='Lookup could not complete';notice(e.message);}}
}
function readingDirty() { $('#save-reading').disabled=false;$('#reading-status').textContent='Unsaved changes'; }
function moveReading(from,to) {
  if(from===to)return;
  const row=readingRows.splice(from,1)[0];readingRows.splice(to,0,row);renderReadingSettings();readingDirty();
  $('#reading-dictionaries').children[to]?.querySelector('input').focus();
}
function renderReadingSettings() {
  const list=$('#reading-dictionaries');list.replaceChildren();
  readingRows.forEach((item,index)=>{
    const row=node('li',undefined,'reading-row');row.draggable=true;
    const label=node('label');const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=item.enabled;
    checkbox.onchange=()=>{item.enabled=checkbox.checked;readingDirty();};label.append(checkbox,node('span',item.title));
    const actions=node('div',undefined,'reorder-actions');
    for(const [text,target,description] of [['⇈',0,'Move to top'],['↑',index-1,'Move up'],['↓',index+1,'Move down']]){
      const button=node('button',text,'secondary');button.disabled=target<0||target>=readingRows.length||target===index;button.setAttribute('aria-label',`${description}: ${item.title}`);button.title=description;button.onclick=()=>moveReading(index,target);actions.append(button);
    }
    row.ondragstart=e=>{e.dataTransfer.setData('text/plain',item.id);e.dataTransfer.effectAllowed='move';};
    row.ondragover=e=>{e.preventDefault();row.classList.add('drop-target');};row.ondragleave=()=>row.classList.remove('drop-target');
    row.ondrop=e=>{e.preventDefault();row.classList.remove('drop-target');const from=readingRows.findIndex(r=>r.id===e.dataTransfer.getData('text/plain'));if(from>=0)moveReading(from,index);};
    row.append(label,actions);list.append(row);
  });
  if(!readingRows.length)list.append(empty('No dictionaries in this group','Add dictionaries to this group from Collection.'));
}
async function loadReadingSettings() {
  if(settingsSaving)return;
  const version=++settingsVersion;const group=$('#settings-group').value||$('#group').value||'Default Group';
  readingGroup=null;readingRows=[];$('#reading-dictionaries').replaceChildren();$('#save-reading').disabled=true;
  $('#reading-status').textContent='Loading settings…';
  try {const data=await api('/reading-settings?'+new URLSearchParams({group}));if(version!==settingsVersion)return;readingGroup=group;readingRows=data.dictionaries;renderReadingSettings();$('#reading-status').textContent=`${readingRows.filter(r=>r.enabled).length} of ${readingRows.length} dictionaries selected`;}
  catch(e){if(version===settingsVersion)$('#reading-status').textContent=e.message;}
}
$('#settings-group').onchange=loadReadingSettings;
for(const [id,enabled] of [['enable-all',true],['disable-all',false]])$( '#'+id).onclick=()=>{if(!readingGroup||settingsSaving)return;for(const row of readingRows)row.enabled=enabled;renderReadingSettings();readingDirty();};
$('#save-reading').onclick=async()=>{
  if(!readingGroup||settingsSaving)return;settingsSaving=true;
  $('#save-reading').disabled=true;$('#settings-group').disabled=true;$('#reading-dictionaries').inert=true;
  const group=readingGroup;
  try {await api('/reading-settings',{method:'PUT',body:JSON.stringify({group,dictionaries:readingRows.map(({id,enabled})=>({id,enabled}))})});$('#reading-status').textContent='Saved. Your next lookup will use this selection and order.';
    // Clear results made with the previous preferences, including playing audio.
    if($('#group').value===group){searchController?.abort();++searchVersion;frames.clear();$('#results').replaceChildren(empty('Reading settings saved','Look up a word to use your updated dictionary selection.'));$('#entry-controls').hidden=true;$('#result-summary').textContent='Your dictionaries, your order.';}
  }catch(e){$('#reading-status').textContent=e.message;$('#save-reading').disabled=false;}
  finally{settingsSaving=false;$('#settings-group').disabled=false;$('#reading-dictionaries').inert=false;}
};
window.addEventListener('message',event=>{const frame=[...frames].find(f=>f.contentWindow===event.source);if(!frame||event.origin!=='null'||event.data?.silverdict!==true)return;const {type,value}=event.data;if(type==='height'&&Number.isFinite(value))frame.style.height=Math.max(160,Math.min(3000,value+25))+'px';if(type==='lookup'&&typeof value==='string'&&value.length<=200)search(value);});
$('#search-form').onsubmit=e=>{e.preventDefault();search($('#query').value);};
window.onpopstate=()=>{const p=new URLSearchParams(location.search);if(p.get('group'))$('#group').value=p.get('group');if(p.get('q'))search(p.get('q'),false);};
(async()=>{await refreshCatalog();refreshJobs();const p=new URLSearchParams(location.search);if(p.get('group'))$('#group').value=p.get('group');if(p.get('q'))search(p.get('q'),false);setInterval(()=>{if(!document.hidden&&(!$('#jobs-view').hidden||jobs.some(j=>['queued','running'].includes(j.status))))refreshJobs();},4000);})();
