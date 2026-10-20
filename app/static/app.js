'use strict';
const $ = (q, el = document) => el.querySelector(q);
const $$ = (q, el = document) => [...el.querySelectorAll(q)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {Books:'▤', Articles:'≡', Music:'♫', Videos:'▷', Movies:'▣', Series:'▥', Advice:'✳', Events:'◷', Appointments:'▦'};
let csrf = '', state = {categories:[], entries:[], jobs:[]}, view = 'home', query = '', tag = '', archived = false;
let authenticated = false, modalKind = '', currentJob = null, toastTimer, refreshing = false;
const dialog = $('#dialog');

async function api(path, body) {
  const response = await fetch('/api/' + path, body === undefined ? {cache:'no-store'} : {
    method:'POST', headers:{'Content-Type':'application/json','X-Requested-With':'ScreenshotManager','X-CSRF-Token':csrf}, body:JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && authenticated) {authenticated = false; closeDialog(); await boot();}
    throw new Error(data.error || 'Request failed');
  }
  return data;
}
function toast(message, error=false) {
  const el = $('#toast'); el.textContent = message; el.className = error ? 'visible error' : 'visible';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => {el.className='';}, 5500);
}
async function boot() {
  try {
    const session = await api('session'); csrf = session.csrf || ''; authenticated = session.authenticated;
    if (authenticated) {await refresh(); return;}
    $('#app').innerHTML = `<main class="welcome"><div class="brand"><span class="brand-mark">▰</span> later<span class="dot">.</span></div>
      <div class="eyebrow">A LITTLE LESS LOST IN YOUR CAMERA ROLL</div><h1>Good things,<br>kept for later.</h1>
      <p class="lead">Books to read. Movies to watch. Advice worth keeping.<br>Your screenshots, turned into a collection.</p>
      <form id="auth-form" class="auth-card"><h2>${session.setup ? 'Make this collection yours' : 'Welcome back'}</h2>
      <p>${session.setup ? 'Create a password for your private collection. Use at least 12 characters.' : 'Enter your collection password to continue.'}</p>
      <label>Password<input name="password" type="password" minlength="12" maxlength="256" autocomplete="${session.setup?'new-password':'current-password'}" required></label>
      ${session.setup?'<label>Confirm password<input name="confirm" type="password" minlength="12" autocomplete="new-password" required></label>':''}
      <button class="primary" type="submit">${session.setup?'Create collection':'Open collection'} <span>→</span></button><p class="form-error" role="alert"></p></form>
      <p class="privacy-note">Stored on your server. You choose the model that processes your screenshots.</p></main>`;
    $('#auth-form').onsubmit = async event => {
      event.preventDefault(); const form = event.currentTarget; const fields = new FormData(form);
      if (session.setup && fields.get('password') !== fields.get('confirm')) { $('.form-error',form).textContent='Passwords do not match.'; return; }
      const button = $('button',form); button.disabled=true;
      try {const result = await api(session.setup?'setup':'login',{password:fields.get('password')}); csrf=result.csrf; authenticated=true; await refresh();}
      catch (error) {$('.form-error',form).textContent=error.message; button.disabled=false;}
    };
  } catch(error) {$('#app').textContent = 'Could not open the app: ' + error.message;}
}
async function refresh() {
  if (refreshing || !authenticated) return;
  refreshing=true;
  try {state=await api('state'); render();} finally {refreshing=false;}
}
function selectedEntries() {
  return state.entries.filter(entry => Boolean(entry.archived) === archived)
    .filter(entry => view==='home' || view==='all' || entry.data.categories.includes(view))
    .filter(entry => !tag || entry.data.tags.includes(tag))
    .filter(entry => !query || JSON.stringify(entry.data).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
}
function render() {
  if (!authenticated) return;
  const focused = document.activeElement?.id === 'search'; const position = focused ? document.activeElement.selectionStart : 0;
  const active = state.entries.filter(e => !e.archived); const ready = state.jobs.filter(j => j.status==='ready').length;
  const entries = selectedEntries();
  const heading = view==='home' ? 'Your collection' : view==='all' ? 'All saved things' : view==='inbox' ? 'Your inbox' : view;
  const subtitle = view==='inbox' ? 'A small pause before a good thing finds its place.' : archived ? 'Finished things, still here when you need them.' : 'Something to read, watch, try, or look forward to.';
  const nav = (id,label,icon,count) => `<button class="nav-item ${view===id?'active':''}" data-view="${escape(id)}"><span>${icon}</span>${escape(label)}${count!==undefined?`<small>${count}</small>`:''}</button>`;
  $('#app').innerHTML = `<div class="shell"><aside class="sidebar"><a href="#" class="brand" data-action="home"><span class="brand-mark">▰</span> later<span class="dot">.</span></a>
    <div class="sidebar-caption">YOUR SCREENSHOT COLLECTION</div><nav aria-label="Collection navigation">
    ${nav('home','Collection','⌂')}${nav('inbox','Inbox','▱',state.jobs.length)}${nav('all','Everything','◫')}<button class="nav-item mobile-settings" data-action="settings"><span>⚙</span>Settings</button>
    <div class="nav-label">CATEGORIES <button data-action="category" aria-label="Add category">+</button></div>
    ${state.categories.map(c => nav(c,c,icons[c]||'◇',active.filter(e=>e.data.categories.includes(c)).length)).join('')}</nav>
    <div class="sidebar-bottom"><div class="private-label"><span class="status-dot"></span> Your private space</div><button class="nav-item" data-action="settings"><span>⚙</span>Settings & backup</button><button class="text-button muted" data-action="logout">Sign out</button></div></aside>
    <main class="main"><header class="topbar"><div class="breadcrumb">My space <span>/</span> ${escape(view==='home'?'Collection':heading)}</div><button class="primary" data-action="upload">＋ Add screenshots</button></header>
    <section class="page-head"><div><div class="eyebrow">${view==='inbox'?'WAITING TO FIND A HOME':'SAVE NOW. ENJOY LATER.'}</div><h1>${escape(heading)}</h1><p>${subtitle}</p></div>
      ${view!=='inbox'?`<button class="secondary" data-action="manual">＋ Add manually</button>`:`<span class="ready-count">${ready} ready to review</span>`}</section>
    ${view==='inbox'?inboxHTML():`${view==='home'&&!query&&!tag&&!archived?`<div class="category-grid">${state.categories.map((c,index)=>`<button class="category-card tint-${index%4}" data-view="${escape(c)}"><span class="category-icon">${icons[c]||'◇'}</span><span>${escape(c)}</span><small>${active.filter(e=>e.data.categories.includes(c)).length} saved <b>↗</b></small></button>`).join('')}</div>`:''}
    ${state.jobs.length&&view==='home'?`<button class="inbox-banner" data-view="inbox"><span>▱</span><div><strong>${state.jobs.length} submission${state.jobs.length===1?'':'s'} in your inbox</strong><small>${ready?`${ready} ready to review`:'Processing happens when your model is available'}</small></div><span>→</span></button>`:''}
    <div class="collection-toolbar"><h2>${view==='home'?'Recently saved':'Saved things'} <span>${entries.length}</span></h2><div class="filters"><label class="search-label"><span>⌕</span><input id="search" aria-label="Search collection" placeholder="Search your collection" value="${escape(query)}"></label><button class="filter-button ${archived?'chosen':''}" data-action="archive-filter">${archived?'Archived':'Active'} ▾</button></div></div>
    ${tag?`<button class="tag" data-action="clear-tag">${escape(tag)} ×</button>`:''}
    <div class="entry-grid">${entries.map(entryHTML).join('')||emptyHTML()}</div>`}
    <footer>Good things deserve a place to come back to.</footer></main></div>`;
  if (focused) {$('#search')?.focus(); $('#search')?.setSelectionRange(position,position);}
  $('#search')?.addEventListener('input',event=>{query=event.target.value;render();});
}
function emptyHTML() {return `<div class="empty"><div class="empty-icon">▰</div><h3>${query||tag?'Nothing matches yet':archived?'Nothing archived yet':'A little room for good things'}</h3><p>${query||tag?'Try another search or category.':'Add a screenshot or a recommendation. Your saved things will appear here.'}</p>${!query&&!tag&&!archived?'<button class="secondary" data-action="upload">Add your first screenshot →</button>':''}</div>`;}
function entryHTML(entry) {
  const d=entry.data; const detail=d.details.slice(0,2).map(p=>p.value).join(' · ');
  return `<article class="entry-card"><div class="entry-top"><span class="entry-symbol">${icons[d.categories[0]]||'◇'}</span><span class="entry-category">${escape(d.categories.join(' / '))}</span><button class="icon-button" data-action="edit" data-id="${entry.id}" aria-label="Edit ${escape(d.title)}">↗</button></div>
    <button class="entry-title" data-action="edit" data-id="${entry.id}">${escape(d.title)}</button>${detail?`<p class="entry-detail">${escape(detail)}</p>`:''}
    ${d.summary?`<p class="entry-summary">${escape(d.summary)}</p>`:''}${d.comment?`<p class="comment-preview">“${escape(d.comment)}”</p>`:''}
    <div class="tags">${d.tags.slice(0,4).map(t=>`<button class="tag" data-tag="${escape(t)}">${escape(t)}</button>`).join('')}</div>
    <div class="entry-bottom"><small>${new Date(entry.created*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'})}${entry.images.length?' · Screenshot saved':''}</small><button class="text-button" data-action="archive-entry" data-id="${entry.id}">${entry.archived?'Restore ↩':d.categories.includes('Movies')||d.categories.includes('Series')?'Watched ✓':d.categories.includes('Books')?'Read ✓':'Archive ✓'}</button></div></article>`;
}
function inboxHTML() {
  const labels={waiting:'Waiting for AI',processing:'Processing',ready:'Ready to review',failed:'Needs attention'};
  return `<div class="inbox-list">${state.jobs.map(job=>`<article class="job-card"><div class="job-image"><img src="/images/${job.images[0]}" alt="Uploaded screenshot" loading="lazy"></div><div class="job-info"><span class="status-chip ${job.status}">${labels[job.status]}</span><h3>${job.images.length>1?`${job.images.length} screenshots · one submission`:'Screenshot submission'}</h3><p>${escape(job.message||'Queued for extraction')}</p>${job.comment?`<p class="comment-preview">${escape(job.comment)}</p>`:''}<small>${new Date(job.created*1000).toLocaleString()}</small><div class="job-actions">${job.status==='ready'?`<button class="primary small" data-action="review" data-id="${job.id}">Review ${job.proposals.length||''} ${job.proposals.length===1?'entry':'entries'} →</button>`:`<button class="secondary small" data-action="job-manual" data-id="${job.id}">Create manually</button>`}<button class="text-button" data-action="job-retry" data-id="${job.id}">Retry extraction</button><button class="text-button" data-action="job-comment" data-id="${job.id}">Edit comment</button><button class="text-button danger" data-action="job-discard" data-id="${job.id}">Discard</button></div></div></article>`).join('')||'<div class="empty"><div class="empty-icon">✓</div><h3>All caught up</h3><p>Your next good find can start here.</p><button class="secondary" data-action="upload">Add screenshots →</button></div>'}</div>`;
}
function openDialog(html,kind) {modalKind=kind;dialog.innerHTML=html;dialog.showModal(); $('input,button,select,textarea',dialog)?.focus();}
function closeDialog(){dialog.close();modalKind='';currentJob=null;}
function modalHeader(title,subtitle=''){return `<div class="modal-head"><div><h2>${title}</h2>${subtitle?`<p>${subtitle}</p>`:''}</div><button class="icon-button" data-action="close" aria-label="Close dialog">×</button></div>`;}
function blankEntry(comment=''){return {title:'',categories:[state.categories.includes(view)?view:state.categories[0]],tags:[],link:'',summary:'',source_text:'',comment,details:[]};}
function fieldHTML(d,index=0){return `<div class="entry-fields" data-index="${index}">
    <label>Title<input name="title" value="${escape(d.title)}" maxlength="500" required placeholder="A movie, a book, a good idea…"></label>
    <fieldset><legend>Categories</legend><div class="category-choices">${state.categories.map(c=>`<label><input type="checkbox" name="categories" value="${escape(c)}" ${d.categories.includes(c)?'checked':''}>${escape(c)}</label>`).join('')}</div></fieldset>
    <div class="two-cols"><label>Link<input name="link" type="url" value="${escape(d.link)}" placeholder="https://…"></label><label>Tags <small>comma separated</small><input name="tags" value="${escape(d.tags.join(', '))}" placeholder="cat food, nutrition"></label></div>
    <label>Personal comment<textarea name="comment" rows="3" maxlength="10000" placeholder="Why you wanted to save this">${escape(d.comment)}</textarea></label>
    <label>Summary<textarea name="summary" rows="3" maxlength="20000">${escape(d.summary)}</textarea></label>
    <fieldset><legend>Useful details</legend><div class="detail-rows">${d.details.map(detailHTML).join('')}</div><button type="button" class="text-button" data-action="add-detail">＋ Add a detail</button><p class="field-hint">Author, artist, date, time, location, source… Leave uncertain details empty.</p></fieldset>
    <details><summary>Relevant source text</summary><textarea name="source_text" rows="8" maxlength="100000">${escape(d.source_text)}</textarea></details></div>`;}
function detailHTML(d){return `<div class="detail-row"><input aria-label="Detail label" class="detail-label" value="${escape(d.label)}" placeholder="Author"><input aria-label="Detail value" class="detail-value" value="${escape(d.value)}" placeholder="Value"><button type="button" class="icon-button" data-action="remove-detail" aria-label="Remove detail">×</button></div>`;}
function readFields(el){return {title:$('[name=title]',el).value,categories:$$('[name=categories]:checked',el).map(i=>i.value),tags:$('[name=tags]',el).value.split(',').map(t=>t.trim()).filter(Boolean),link:$('[name=link]',el).value,comment:$('[name=comment]',el).value,summary:$('[name=summary]',el).value,source_text:$('[name=source_text]',el).value,details:$$('.detail-row',el).map(row=>({label:$('.detail-label',row).value,value:$('.detail-value',row).value})).filter(d=>d.label||d.value)};}
function imagesHTML(images){return `<div class="review-images">${images.map(name=>`<a href="/images/${name}" target="_blank" rel="noopener"><img src="/images/${name}" alt="Source screenshot" loading="lazy"><span>Open full image ↗</span></a>`).join('')}</div>`;}
function duplicateOptions(d){
  const words=d.title.toLocaleLowerCase().replace(/[^\p{L}\p{N} ]/gu,'').split(/\s+/).filter(Boolean);
  return state.entries.filter(e=>{const title=e.data.title.toLocaleLowerCase();return words.length&&words.filter(w=>title.includes(w)).length/words.length>=0.6;})
    .map(e=>`<option value="${e.id}">Update: ${escape(e.data.title)}${e.archived?' (archived)':''}</option>`).join('');
}
function review(job){currentJob=job; const proposals=job.proposals.length?job.proposals:[blankEntry(job.comment)];
  openDialog(`${modalHeader('A good thing to keep','Check the details before they join your collection.')}<form id="review-form"><div class="review-layout">${imagesHTML(job.images)}<div class="review-proposals">${proposals.map((d,index)=>`<section class="proposal"><label class="keep-proposal"><input type="checkbox" name="keep" checked> Save this entry</label>${fieldHTML(d,index)}<label>Possible duplicate?<select name="merge"><option value="">Save as a separate entry</option>${duplicateOptions(d)}</select></label><p class="field-hint">Updating a match preserves its existing comments and appends your new comment. Uncheck “Save this entry” to skip this proposal.</p></section>`).join('')}</div></div><div class="modal-footer"><label class="check-label"><input type="checkbox" name="keep_images" checked> Keep original screenshots</label><button class="primary" type="submit">Save selected entries →</button></div><p class="form-error" role="alert"></p></form>`,'review');
  $('#review-form').onsubmit=async event=>{event.preventDefault(); const form=event.currentTarget;const items=$$('.proposal',form).filter(p=>$('[name=keep]',p).checked).map(p=>({data:readFields(p),merge_id:$('[name=merge]',p).value||null}));await submitForm(form,async()=>{await api('review',{id:job.id,generation:job.generation,items,keep_images:$('[name=keep_images]',form).checked});closeDialog();toast('Saved to your collection');await refresh();});};
}
async function submitForm(form,operation){const button=$('[type=submit]',form);if(button)button.disabled=true;$('.form-error',form).textContent='';try{await operation();}catch(error){$('.form-error',form).textContent=error.message;}finally{if(button)button.disabled=false;}}
function edit(entry){const d=entry?.data||blankEntry();openDialog(`${modalHeader(entry?'Your saved thing':'Save something new')}<form id="entry-form">${entry?.images.length?imagesHTML(entry.images):''}${fieldHTML(d)}<div class="modal-footer"><div>${entry?`<button class="text-button danger" type="button" data-action="delete-entry" data-id="${entry.id}">Delete entry</button>${entry.images.length?`<button class="text-button" type="button" data-action="remove-images" data-id="${entry.id}">Remove images</button>`:''}`:''}</div><button class="primary" type="submit">Save changes →</button></div><p class="form-error" role="alert"></p></form>`,'edit');
  $('#entry-form').onsubmit=event=>{event.preventDefault();const form=event.currentTarget;submitForm(form,async()=>{await api('entry',{id:entry?.id,action:'save',data:readFields(form)});closeDialog();toast('Saved');await refresh();});};
}
function fileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read file'));reader.readAsDataURL(file);});}
function upload(){openDialog(`${modalHeader('Found something good?','Add screenshots now. Review them when you have a moment.')}<form id="upload-form"><label class="dropzone"><span>＋</span><strong>Choose screenshots</strong><small>PNG or JPEG · up to 10 images · 8 MB each / 20 MB total</small><input name="files" type="file" accept="image/png,image/jpeg" multiple required></label><div id="upload-files"></div><label class="check-label grouping"><input type="checkbox" name="grouped"> These screenshots belong to one item or post</label><label>Comment for the grouped submission<textarea name="comment" rows="3" placeholder="For example: save only the second book"></textarea></label><p class="field-hint">For separate items, use the individual comments above. Comments help guide extraction.</p><div class="modal-footer"><span>Saved on your server before processing.</span><button class="primary" type="submit">Add to inbox →</button></div><p class="form-error" role="alert"></p></form>`,'upload');
  $('[name=files]',dialog).onchange=event=>{$('#upload-files').innerHTML=[...event.target.files].map((file,index)=>`<label class="file-comment">${escape(file.name)}<input data-file-comment="${index}" placeholder="Optional comment for this screenshot"></label>`).join('');};
  $('#upload-form').onsubmit=event=>{event.preventDefault();const form=event.currentTarget;submitForm(form,async()=>{const files=[...$('[name=files]',form).files];if(files.length>10||files.some(f=>f.size>8*1024*1024)||files.reduce((n,f)=>n+f.size,0)>20*1024*1024)throw new Error('Limit: 10 images, 8 MB each, 20 MB total');const prepared=[];for(let i=0;i<files.length;i++)prepared.push({data:await fileBase64(files[i]),comment:$(`[data-file-comment="${i}"]`,form)?.value||''});await api('upload',{files:prepared,grouped:$('[name=grouped]',form).checked,comment:$('[name=comment]',form).value});closeDialog();view='inbox';toast('Screenshots saved. Extraction will run when your model is available.');await refresh();});};
}
const providerLabels = {lmstudio:'LM Studio (local)',openrouter:'OpenRouter (free models)',google:'Google AI / Gemini (free tier)',huggingface:'Hugging Face (limited free credits)'};
const providerGuidance = {
  lmstudio:'Uses your already-loaded local model. No automatic model loading or provider switching.',
  openrouter:'Only openrouter/free or :free models are allowed. The free router chooses an available free model that supports the request. Free quotas vary; no paid model fallback.',
  google:'Use a Google AI Studio API key from a free-tier project with billing disabled. The app cannot inspect your billing tier. Google’s free-tier data may be used to improve its products.',
  huggingface:'Free accounts currently receive only $0.10/month in inference credits, subject to change. Use a free account without purchased credits, paid plans, or custom paid-provider keys. Choose an available model from the Inference Providers playground.'
};
function providerFields(s,name){const p=s.providers[name];const cloud=name!=='lmstudio';return `<div class="section-label">${escape(providerLabels[name].toUpperCase())}</div>${name==='lmstudio'?`<label>Server address<input name="url" type="url" required value="${escape(p.url)}"></label>`:''}<label>Model identifier${name==='google'?`<select name="model">${s.google_models.map(model=>`<option value="${escape(model)}" ${p.model===model?'selected':''}>${escape(model)}${model.startsWith('gemini-2.5-')?' (legacy; may reject new accounts)':''}</option>`).join('')}</select>`:`<input name="model" required value="${escape(p.model)}" placeholder="${name==='huggingface'?'Organization/model (from HF playground)':''}">`}</label><label>${cloud?'API key / token':'API token (optional)'}<input name="token" type="password" autocomplete="off" value="${escape(p.draft_token||'')}" placeholder="${p.has_token?'Key saved for this provider; leave blank to keep it.':'Enter this provider’s own key'}"></label>${p.has_token?'<label class="check-label"><input name="clear_token" type="checkbox"> Remove this provider’s saved key</label>':''}<div class="two-cols"><label>Extraction mode<select name="mode"><option value="auto" ${p.mode==='auto'?'selected':''}>Auto / image input</option><option value="vision" ${p.mode==='vision'?'selected':''}>Image input only</option><option value="ocr" ${p.mode==='ocr'?'selected':''}>Local OCR + text model</option></select></label><label>Request timeout (seconds)<input name="timeout" type="number" min="30" max="600" value="${s.timeout}"></label></div><p class="field-hint">${providerGuidance[name]}</p>${cloud?`<label class="check-label external-consent"><input name="external_confirmed" type="checkbox" required> Send pending and future submissions to ${escape(providerLabels[name].split(' (')[0])}. I have configured a free account as described above. Screenshots or OCR text and comments leave my server.</label>`:''}`;}
async function settings(){const s=await api('settings');let edited=s.provider;openDialog(`${modalHeader('Your setup','One private collection. Your choice of model.')}<form id="settings-form"><label class="check-label"><input type="checkbox" name="auto_approve" ${s.auto_approve?'checked':''}> Auto-approve</label><p class="field-hint">Save future AI results directly to your collection, keeping screenshots and personal comments. Entries remain editable and can be archived or deleted. Existing review cards stay in the Inbox.</p><label>AI provider<select name="provider">${Object.entries(providerLabels).map(([id,label])=>`<option value="${id}" ${edited===id?'selected':''}>${label}</option>`).join('')}</select></label><p id="active-provider" class="field-hint">Currently active: ${escape(providerLabels[s.provider])}. Select a provider and save to switch processing.</p><div id="provider-fields" class="provider-fields">${providerFields(s,edited)}</div><div class="modal-footer"><button class="secondary" type="button" data-action="model-test">Test active provider</button><button class="primary" type="submit">Save settings</button></div><p id="model-result" role="status"></p><p class="form-error" role="alert"></p></form><hr><div class="section-label">IPHONE SHORTCUT</div><p>Send photos using Share → Later. This token can only upload images.</p><p id="shortcut-status">${s.has_shortcut_token?'A Shortcut token is active. Creating another replaces it.':'No Shortcut token is active.'}</p><button class="secondary" data-action="shortcut-create">Create new token</button> <button class="text-button danger" data-action="shortcut-revoke">Revoke token</button><div id="shortcut-details" hidden><label>Upload URL<input id="shortcut-url" readonly></label><label>Token — copy now; shown only once<input id="shortcut-token" readonly autocomplete="off"></label><p>Keep this token private. In Shortcuts, send a converted JPEG using POST, Request Body: File, with Authorization: Bearer YOUR_TOKEN.</p></div><hr><div class="section-label">CATEGORIES</div><div class="settings-categories">${state.categories.map(c=>`<button class="tag" data-action="rename-category" data-name="${escape(c)}">${escape(c)} ✎</button>`).join('')}<button class="text-button" data-action="category">＋ Add category</button></div><hr><div class="section-label">BACKUP & RESTORE</div><p>Exports include entries, comments, screenshots, and pending uploads. Passwords and provider credentials stay on this server.</p><a class="secondary link-button" href="/api/export" download>Download backup ↓</a><form id="restore-form"><label>Restore a backup ZIP<input name="backup" type="file" accept=".zip" required></label><p class="field-hint">Browser restore supports backups up to 20 MB. Larger collections: use the server backup instructions.</p><label>Type REPLACE to confirm replacing your current collection<input name="confirm" pattern="REPLACE" required autocomplete="off"></label><button class="secondary danger" type="submit">Replace collection from backup</button><p class="form-error" role="alert"></p></form><hr><button class="text-button" data-action="logout">Sign out of this collection</button>`,'settings');
  const remember=()=>{const form=$('#settings-form');const p=s.providers[edited];for(const key of ['url','model','mode']){const field=$(`[name=${key}]`,form);if(field)p[key]=field.value;}p.draft_token=$('[name=token]',form).value;s.timeout=Number($('[name=timeout]',form).value);};
  $('[name=provider]',dialog).onchange=event=>{remember();edited=event.target.value;$('#provider-fields').innerHTML=providerFields(s,edited);$('#model-result').textContent='';$('.form-error',$('#settings-form')).textContent='';};
  $('#settings-form').onsubmit=event=>{event.preventDefault();const form=event.currentTarget;const f=new FormData(form);submitForm(form,async()=>{await api('settings',{provider:edited,url:f.get('url'),model:f.get('model'),token:f.get('token'),clear_token:f.has('clear_token'),mode:f.get('mode'),timeout:Number(f.get('timeout')),external_confirmed:f.has('external_confirmed'),auto_approve:f.has('auto_approve')});remember();s.providers[edited].draft_token='';const updated=await api('settings');s.providers=updated.providers;s.provider=updated.provider;$('#provider-fields').innerHTML=providerFields(s,edited);$('#active-provider').textContent=`Currently active: ${providerLabels[s.provider]}. No automatic fallback.`;toast('Settings saved');await refresh();});};
  $('#restore-form').onsubmit=event=>{event.preventDefault();const form=event.currentTarget;submitForm(form,async()=>{const file=$('[name=backup]',form).files[0];if(file.size>20*1024*1024)throw new Error('Use server-side backup instructions for files over 20 MB');if(!confirm('Replace all current entries and pending uploads? Download a backup first if you want to keep them.'))return;await api('restore',{confirm:$('[name=confirm]',form).value,data:await fileBase64(file)});closeDialog();view='home';query='';tag='';toast('Collection restored');await refresh();});};
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action],[data-view],[data-tag]');if(!button)return;
  event.preventDefault();
  try {
    if(button.dataset.view){view=button.dataset.view;query='';tag='';archived=false;render();return;}
    if(button.dataset.tag){tag=button.dataset.tag;render();return;}
    const action=button.dataset.action,id=button.dataset.id,entry=state.entries.find(e=>e.id===id),job=state.jobs.find(j=>j.id===id);
    if(action==='home'){view='home';query='';tag='';archived=false;render();}
    else if(action==='close')closeDialog();
    else if(action==='upload')upload();
    else if(action==='manual')edit();
    else if(action==='edit')edit(entry);
    else if(action==='review')review(job);
    else if(action==='settings')await settings();
    else if(action==='archive-filter'){archived=!archived;render();}
    else if(action==='clear-tag'){tag='';render();}
    else if(action==='archive-entry'){await api('entry',{id,action:'archive',archived:!entry.archived});toast(entry.archived?'Restored to active collection':'Archived');await refresh();}
    else if(action==='delete-entry'){if(confirm('Permanently delete this entry? This cannot be undone.')){await api('entry',{id,action:'delete'});closeDialog();await refresh();toast('Entry deleted');}}
    else if(action==='remove-images'){if(confirm('Remove the screenshots from this entry? The saved text will remain.')){await api('entry',{id,action:'remove_images'});await refresh();edit(state.entries.find(e=>e.id===id));}}
    else if(action==='job-discard'){if(confirm('Discard this submission and its unsaved proposals?')){await api('job',{id,action:'discard'});await refresh();}}
    else if(action==='job-retry'){await api('job',{id,action:'retry'});await refresh();toast('Queued again');}
    else if(action==='job-manual'){await api('job',{id,action:'manual'});await refresh();review(state.jobs.find(j=>j.id===id));}
    else if(action==='job-comment'){const comment=prompt('Edit your comment. Changing it queues a fresh extraction.',job.comment);if(comment!==null){await api('job',{id,action:'comment',comment});await refresh();}}
    else if(action==='category'||action==='rename-category'){const old=button.dataset.name;const name=prompt(old?'Rename category':'New category name',old||'');if(name?.trim()){await api('category',{name:name.trim(),old});if(view===old)view=name.trim();await refresh();if(dialog.open)closeDialog();toast('Category saved');}}
    else if(action==='add-detail'){button.closest('fieldset').querySelector('.detail-rows').insertAdjacentHTML('beforeend',detailHTML({label:'',value:''}));}
    else if(action==='remove-detail'){button.closest('.detail-row').remove();}
    else if(action==='shortcut-create'){if(!confirm('Create a new token? Any existing Later shortcut will need the new token.'))return;const result=await api('shortcut-token',{action:'create'});$('#shortcut-details').hidden=false;$('#shortcut-url').value=location.origin+'/api/shortcut-upload';$('#shortcut-token').value=result.token;$('#shortcut-status').textContent='Token active. Copy it now; it is shown only once.';}
    else if(action==='shortcut-revoke'){await api('shortcut-token',{action:'revoke'});$('#shortcut-details').hidden=true;$('#shortcut-token').value='';$('#shortcut-status').textContent='Token revoked. Shortcut uploads are disabled.';toast('Shortcut token revoked');}
    else if(action==='model-test'){button.disabled=true;try{const result=await api('model-test',{});$('#model-result').textContent=result.model.external?`Connected: ${result.model.name}. Key and model access checked; extraction compatibility is checked on upload.`:`Connected: ${result.model.name}. ${result.model.vision?'Image input supported.':'Text-only: OCR will run first.'}`;}catch(error){$('#model-result').textContent=error.message;}finally{button.disabled=false;}}
    else if(action==='logout'){await api('logout',{});if(dialog.open)closeDialog();authenticated=false;await boot();}
  }catch(error){toast(error.message,true);}
});
dialog.addEventListener('cancel',()=>{modalKind='';currentJob=null;});
setInterval(()=>{if(authenticated&&!document.hidden)refresh().catch(error=>toast(error.message,true));},8000);
boot();
