'use strict';
const $ = s => document.querySelector(s);
const state = {jobs: [], selected: '', pending: false, detailShown: new Set(), fingerprints: {}};
const statusNames = {queued:'排队中',running:'正在处理',waiting_captcha:'等待验证码',done:'已完成',failed:'未完成',cancelled:'已取消'};
const terminal = s => ['done','failed','cancelled'].includes(s);
function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent=text; if(cls)node.className=cls; return node; }
function notice(message) { $('#notice').textContent=message; $('#notice').hidden=!message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  if(!response.ok) { let body; try{body=await response.json();}catch{body={detail:response.statusText};} throw new Error(typeof body.detail==='string'?body.detail:'请求参数无效，请检查输入'); }
  return response.json();
}
function fileURL(path) {return '/api/files/'+path.split('/').map(encodeURIComponent).join('/');}
function updateResults() {
  const searches=state.jobs.filter(j=>j.kind==='search');
  if(!searches.some(j=>j.id===state.selected))state.selected=searches[0]?.id||'';
  const history=$('#search-history');history.replaceChildren();
  for(const j of searches){const o=el('option',j.title+' · '+statusNames[j.status]);o.value=j.id;history.append(o);}
  if(!searches.length){const o=el('option','暂无搜索');o.value='';history.append(o);}
  history.value=state.selected;
  const job=searches.find(j=>j.id===state.selected), box=$('#results');
  const fingerprint=JSON.stringify([job?.id,job?.status,job?.results,job?.errors]);
  if(state.fingerprints.results===fingerprint)return;
  state.fingerprints.results=fingerprint;
  $('#result-count').textContent=job?.results.length||0;
  $('#source-errors').replaceChildren();
  if(job && Object.keys(job.errors).length) {
    for(const [source,message] of Object.entries(job.errors))$('#source-errors').append(el('div',source+'：'+message,'warning'));
  }
  if(!job)return;
  box.replaceChildren();box.className='panel';
  if(!job.results.length){box.classList.add('empty');box.append(el('div',terminal(job.status)?'文':'…','empty-symbol'),el('h3',job.status==='done'?'未找到匹配字幕':statusNames[job.status]),el('p',job.error|| (job.status==='done'?'试试其他片名、语言或字幕源。':'可以继续搜索其他关键词；任务会依次处理。')));return;}
  for(const item of job.results) {
    const row=el('div',undefined,'result-row'),info=el('div',undefined,'result-info'), actions=el('div',undefined,'result-actions');
    info.append(el('div',item.title,'result-title'),el('div',[item.fmt||'格式未知',item.language||'语言未知',item.downloads?`${item.downloads} 次下载`:'',item.uploaded||''].filter(Boolean).join(' · '),'result-meta'));
    const download=el('button','下载 ↓','download-button');download.onclick=()=>startItem('/api/download',job.id,item.id,download);
    const detail=el('button','查看详情','text-button');detail.onclick=()=>startItem('/api/detail',job.id,item.id,detail);
    actions.append(download,detail);row.append(el('span',item.source_label||item.source,'source-tag'),info,actions);box.append(row);
  }
}
async function startItem(path,search_id,item_id,button) {
  button.disabled=true;
  try{await api(path,{search_id,item_id});notice('');await poll();}catch(e){notice(e.message);}finally{button.disabled=false;}
}
function updateTasks() {
  const box=$('#tasks');
  const jobs=state.jobs.slice(0,12);
  const liveIds=new Set(jobs.map(j=>j.id));
  for(const n of [...box.children])if(!liveIds.has(n.dataset.job))n.remove();
  for(const job of jobs) {
    let card=document.getElementById('job-'+job.id);
    if(!card){card=el('div',undefined,'task');card.id='job-'+job.id;card.dataset.job=job.id;box.append(card);}
    const fingerprint=JSON.stringify(job);
    // Preserve focused CAPTCHA input across polling updates.
    const challenge=card.querySelector('.captcha');
    const preserve=challenge?.dataset.challenge===job.challenge?.id && job.challenge;
    if(state.fingerprints[job.id]===fingerprint)continue;
    state.fingerprints[job.id]=fingerprint;
    if(preserve)challenge.remove();
    card.replaceChildren(el('div',(job.kind==='download'?'下载 · ':job.kind==='detail'?'详情 · ':'搜索 · ')+job.title,'task-title'));
    const status=el('div',undefined,'task-status '+job.status);status.append(el('span',statusNames[job.status]));
    if(!terminal(job.status)){const cancel=el('button','取消','text-button');cancel.onclick=()=>api('/api/jobs/'+job.id+'/cancel',{}).then(poll).catch(e=>notice(e.message));status.append(cancel);}card.append(status);
    if(job.challenge)card.append(preserve?challenge:captchaForm(job));
    for(const message of job.warnings||[])card.append(el('p',message,'warning'));
    if(job.error)card.append(el('pre',job.error));
    if(job.logs.length){const details=el('details'),summary=el('summary','查看日志');details.append(summary,el('pre',job.logs.join('\n')));card.append(details);}
    for(const path of job.files){const link=el('a',path.split('/').pop(),'text-button');link.href=fileURL(path);card.append(link,el('br'));}
    if(job.kind==='detail'&&job.status==='done'&&job.detail&&!state.detailShown.has(job.id)){
      state.detailShown.add(job.id);showDetail(job.detail);
    }
  }
  // Keep new jobs first without replacing their DOM nodes.
  jobs.forEach(j=>box.append(document.getElementById('job-'+j.id)));
}
function showDetail(data) {
  const content=$('#detail-content');content.replaceChildren(el('h3',data.title));
  const fields=el('dl',undefined,'detail-fields');
  for(const [key,label] of Object.entries({fmt:'字幕格式',language:'语言',rating:'评分',downloads:'下载次数',uploaded:'上传时间',file_size:'文件大小',maker:'制作',reviser:'校订'})) {
    if(data[key])fields.append(el('dt',label),el('dd',String(data[key])));
  }
  content.append(fields);
  if(data.intro)content.append(el('p',data.intro));
  if(data.related?.length){content.append(el('h4','相关字幕'));const list=el('ul');data.related.forEach(item=>list.append(el('li',item.title)));content.append(list);}
  if(!$('#detail-dialog').open)$('#detail-dialog').showModal();
}
function captchaForm(job) {
  const form=el('form',undefined,'captcha');form.dataset.challenge=job.challenge.id;
  const image=el('img');image.src=job.challenge.image;image.alt='5 位数字验证码';
  const input=el('input');input.inputMode='numeric';input.pattern='[0-9]{5}';input.maxLength=5;input.required=true;input.autocomplete='off';input.setAttribute('aria-label','验证码');
  const buttons=el('div',undefined,'buttons'),submit=el('button','确认','download-button'),refresh=el('button','换一张','text-button');refresh.type='button';
  async function send(action){try{await api('/api/jobs/'+job.id+'/captcha',{challenge_id:job.challenge.id,action,answer:input.value});await poll();}catch(e){notice(e.message);}}
  form.onsubmit=e=>{e.preventDefault();send('answer');};refresh.onclick=()=>send('refresh');buttons.append(submit,refresh);form.append(el('label','请输入图片中的 5 位数字（3 分钟内）'),image,input,buttons);return form;
}
async function refreshFiles() {
  const files=await api('/api/files'),box=$('#files');$('#file-count').textContent=files.length;box.replaceChildren();
  if(!files.length)box.append(el('p','还没有下载文件。','muted'));
  for(const file of files){const row=el('div',undefined,'file-row'),link=el('a',file.name);link.href=fileURL(file.path);row.append(link,el('span',file.size<1024?file.size+' B':(file.size/1024).toFixed(1)+' KB','file-size'));box.append(row);}
}
async function poll() {
  if(state.pending)return;state.pending=true;
  try{state.jobs=await api('/api/jobs');$('#connection').textContent='● 服务已连接';updateResults();updateTasks();}
  catch(e){$('#connection').textContent='连接中断';notice(e.message);}finally{state.pending=false;}
}
$('#search-form').onsubmit=async e=>{
  e.preventDefault();const sources=[...document.querySelectorAll('input[name=sources]:checked')].map(n=>n.value);
  if(!sources.length){notice('请至少选择一个字幕来源');return;}
  const button=$('#search-button');button.disabled=true;
  try{const result=await api('/api/search',{keyword:$('#keyword').value,sources,fmt:$('#fmt').value,language:$('#language').value,limit:Number($('#limit').value),use_ocr:$('#use-ocr').checked});state.selected=result.id;notice('');await poll();}
  catch(e){notice(e.message);}finally{button.disabled=false;}
};
$('#search-history').onchange=e=>{state.selected=e.target.value;updateResults();};
$('#refresh-files').onclick=()=>refreshFiles().catch(e=>notice(e.message));
$('#close-detail').onclick=()=>$('#detail-dialog').close();
async function init(){try{const settings=await api('/api/settings');for(const key of ['keyword','fmt','language','limit'])$('#'+key).value=settings[key];$('#use-ocr').checked=settings.use_ocr;document.querySelectorAll('input[name=sources]').forEach(n=>n.checked=settings.sources.includes(n.value));await refreshFiles();}catch(e){notice(e.message);}await poll();setInterval(poll,1600);setInterval(()=>refreshFiles().catch(()=>{}),10000);}
init();
