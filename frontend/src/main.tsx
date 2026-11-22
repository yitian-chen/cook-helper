import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './style.css';

type Pref={version:number;allergens:string[];excluded_ingredient_ids:string[];dietary_rules:string[];servings:number};
type Ingredient={ingredient_id:string;name:string;amount:number;unit:string};
type Dish={id:string;name:string;course:string;portions:number;ingredients:Ingredient[]};
type Kitchen={cooks:number;burner:number;wok:number;pot:number;steamer:number;rice_cooker:number;oven:number};
type Task={id:string;dish:string;action:string;start:number;end:number;after:string[];hands:number};
type Message={role:'user'|'assistant';content:string;image?:string};
type Session={id:string;title:string};
const currentRun=(id:string)=>api<Run>('/v2/current?session_id='+encodeURIComponent(id));
type Quantity={name:string;unit:string;amount:number};
type Alternative={id:string;name:string;reason:string;minutes:number;added:Quantity[];saved:Quantity[];needs_shopping:boolean;equipment:string[]};
type Preview={dish_id:string;options:Alternative[]};
type Run={swap_preview?:Preview|null;undo_swap?:{menu:{dishes:Dish[]}}|null;session_id:string;revision:number;preferences_version:number;messages:Message[];ready:boolean;
 menu:{version:number;people:number;dishes:Dish[]}|null;
 timeline:{id:string;minutes:number;steps:Task[];done:string[]}|null;kitchen?:Kitchen|null};
async function api<T>(path:string, body?:unknown, method?:string):Promise<T>{
 const res=await fetch('/api'+path,{method:method??(body?'POST':'GET'),headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 const data=await res.json(); if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查后重试'); return data;
}
type ToolProgress = {id: string; name: string; status: 'running' | 'completed' | 'failed'};
type AgentEvent = ({type: 'tool'} & ToolProgress) | {type: 'result'; result: Run} | {type: 'error'; detail: string};

const toolLabels: Record<string, string> = {
 recognize_ingredients: '识别食材',
 write_inventory: '更新食材库存',
 compose_menu:'搭配整餐',replace_dish:'比较替代菜',choose_dish:'更新餐单',undo_swap:'恢复原菜',confirm_ready:'确认备餐',generate_timeline:'编排烹饪时间线',
 generate_shopping_list: '生成待买清单',
 analyze_nutrition: '估算营养',
};
const toolStatus = {running: '执行中', completed: '已完成', failed: '失败'};

async function agentRequest(path: string, body: object, onTool: (tool: ToolProgress) => void): Promise<Run> {
 const response = await fetch('/api' + path, {
  method: 'POST',
  headers: {'Content-Type': 'application/json', Accept: 'application/x-ndjson'},
  body: JSON.stringify(body),
 });
 if (!response.ok) {
  const error = await response.json();
  throw new Error(typeof error.detail === 'string' ? error.detail : '请求失败，请重试');
 }
 if (!response.body) throw new Error('未收到助手的响应');
 const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
 let pending = '';
 try {
  while (true) {
   const {value, done} = await reader.read();
   if (done) break;
   pending += value;
   const lines = pending.split('\n');
   pending = lines.pop() ?? '';
   for (const line of lines) {
    if (!line.trim()) continue;
    const event: AgentEvent = JSON.parse(line);
    if (event.type === 'tool') onTool(event);
    if (event.type === 'error') throw new Error(event.detail);
    if (event.type === 'result') return event.result;
   }
  }
  throw new Error('助手连接已中断，请重试');
 } finally {
  await reader.cancel().catch(() => {});
  reader.releaseLock();
 }
}

const allergyLabels:Record<string,string>={egg:'蛋类',soy:'大豆',wheat:'小麦',milk:'乳制品',peanut:'花生',fish:'鱼类',shellfish:'甲壳贝类',sesame:'芝麻'};
const units:Record<string,string>={g:'克',piece:'个',ml:'毫升'};
const roles:Record<string,string>={main:'主菜',side:'配菜',soup:'汤',staple:'主食'};
const kitchenLabels:Record<keyof Kitchen,string>={cooks:'下厨人数',burner:'灶眼',wok:'炒锅',pot:'汤锅',steamer:'蒸锅',rice_cooker:'电饭锅',oven:'烤箱'};
const initialKitchen:Kitchen={cooks:1,burner:1,wok:1,pot:1,steamer:0,rice_cooker:1,oven:0};

const welcome:Message={role:'assistant',content:'今天想做什么菜？可以告诉我手边有哪些食材，或发一张厨房台面的照片。'};
function App(){
 const [sidebarCollapsed,setSidebarCollapsed]=useState(()=>localStorage.getItem('cook-helper.sidebar-collapsed')==='true');
 function toggleSidebar(){
  const collapsed=!sidebarCollapsed;
  setSidebarCollapsed(collapsed);
  localStorage.setItem('cook-helper.sidebar-collapsed',String(collapsed));
 }
 const sessionDrawer=useRef<HTMLDialogElement>(null);
 const [sessions,setSessions]=useState<Session[]>([]);
 const [catalog,setCatalog]=useState<Record<string,{name:string}>>({});
 const [prefs,setPrefs]=useState<Pref|null>(null),[saved,setSaved]=useState<Pref|null>(null);
 const [messages,setMessages]=useState<Message[]>([welcome]),[draft,setDraft]=useState('');
 const [file,setFile]=useState<File|null>(null),[run,setRun]=useState<Run|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const [preview,setPreview]=useState<Preview|null>(null),[swapRequest,setSwapRequest]=useState('');
 const panel=useRef<HTMLDivElement>(null);
 const [progress,setProgress]=useState<ToolProgress[]|null>(null);
 const dialog=useRef<HTMLDialogElement>(null),end=useRef<HTMLDivElement>(null),picker=useRef<HTMLInputElement>(null);
 const kitchenDialog=useRef<HTMLDialogElement>(null);
 const [equipment,setEquipment]=useState<Kitchen>(initialKitchen);
 const [storedKitchen,setStoredKitchen]=useState<Kitchen|null>(null);
 function applyState(next:Run){
  setPreview(next.swap_preview??null);setSwapRequest('');
  setRun(next);setMessages([welcome,...next.messages]);
 }
 function openKitchen(){setEquipment(storedKitchen??initialKitchen);kitchenDialog.current?.showModal()}
 const dirty=JSON.stringify(prefs)!==JSON.stringify(saved);
 const append=(m:Message)=>setMessages(previous=>[...previous,m]);
 const working=busy;
 async function loadSession(id:string){
  const [state,p]=await Promise.all([currentRun(id),api<Pref>('/preferences')]);
  setSaved(p);setPrefs(p);setStoredKitchen(state.kitchen??null);applyState(state);
  localStorage.setItem('cook-helper.session',id);
  setDraft('');setFile(null);setProgress(null);
 }
 useEffect(()=>{
  async function initialize(){
   const [c,entries]=await Promise.all([api<typeof catalog>('/ingredients'),api<Session[]>('/v2/sessions')]);
   setCatalog(c);setSessions(entries);
   const last=localStorage.getItem('cook-helper.session');
   await loadSession(entries.find(entry=>entry.id===last)?.id??entries[0].id);
  }
  initialize().catch(e=>setError(e.message));
 },[]);
 async function switchSession(id?:string){
  if(id && id===run?.session_id){sessionDrawer.current?.close();return}
  await action(async()=>{
   if(!id){
    const entry=await api<Session>('/v2/sessions',{},'POST');
    setSessions(entries=>[entry,...entries]);
    id=entry.id;
   }
   await loadSession(id);
   sessionDrawer.current?.close();
  });
 }
 useEffect(()=>{if(preview)panel.current?.scrollIntoView({block:'nearest'});else end.current?.scrollIntoView({block:'end'})},[messages,progress,preview]);
 async function action(fn:()=>Promise<void>){setError('');setBusy(true);try{await fn()}catch(e){setError((e as Error).message);try{const [state,p]=await Promise.all([currentRun(run!.session_id),api<Pref>('/preferences')]);applyState(state);setStoredKitchen(state.kitchen??null);setSaved(p);setPrefs(p)}catch{/* keep the original error */}}finally{setBusy(false)}}
 async function askAgent(path: string, body: object) {
  setProgress([]);
  try {
   return await agentRequest(path, {...body,session_id:run!.session_id}, event => setProgress(current => {
    const tools = current ?? [];
    return tools.some(tool => tool.id === event.id)
     ? tools.map(tool => tool.id === event.id ? event : tool)
     : [...tools, event];
   }));
  } finally {
   setProgress(null);
  }
 }
 async function save(){
  if(!prefs)return;
  const {version,...body}=prefs;
  const updated=await api<Pref>('/preferences',body,'PUT');
  setSaved(updated);setPrefs(updated);
  const next=await currentRun(run!.session_id);applyState(next);dialog.current?.close();
  if(run?.menu)applyState(await askAgent('/v2/turn',{action:'replan',revision:next.revision,preferences_version:updated.version}));
 }
 const change=(patch:Partial<Pref>)=>{if(prefs)setPrefs({...prefs,...patch})};
 const toggle=(list:string[],value:string)=>list.includes(value)?list.filter(x=>x!==value):[...list,value];
 async function send(){
  if(working||(!draft.trim()&&!file)||!saved)return;
  await action(async()=>{
   const content=draft.trim();
   let image_id:string|undefined;
   if(file){const form=new FormData();form.append('file',file);image_id=(await api<{image_id:string}>('/images',form)).image_id}
   const message:Message={role:'user',content:content||'请识别照片中的食材。',image:image_id};
   append(message);setDraft('');setFile(null);
   const next=await askAgent('/v2/turn',{revision:run?.revision??0,image_id,text:content,preferences_version:saved.version});
   applyState(next);
   if(next.ready&&!next.timeline&&!storedKitchen) openKitchen();
  })
 }
 function version(){
  return {session_id:run!.session_id,revision:run!.revision,preferences_version:run!.preferences_version};
 }
 async function openAlternatives(dish:Dish){
  if(preview?.dish_id===dish.id){setPreview(null);return}
  await action(async()=>{
   const options=await api<Preview>('/v2/alternatives',{...version(),dish_id:dish.id});
   setSwapRequest('');setPreview(options);
  });
 }
 async function menuAction(kind:'replace'|'choose'|'undo'|'ready'|'schedule',dish?:Dish,target?:Alternative){
  if(!run)return;
  await action(async()=>{
   const next=await askAgent('/v2/turn',{...version(),action:kind,dish_id:dish?.id,target_id:target?.id,
    text:kind==='replace'?swapRequest.trim():undefined});
   applyState(next);
   if(kind==='ready'&&next.ready&&!next.timeline&&!storedKitchen)openKitchen();
  });
 }
 const quantities=(rows:Quantity[])=>rows.map(q=>q.name+' '+q.amount+(units[q.unit]??q.unit)).join('、');
 async function saveKitchen(){
  if(!run)return;
  await action(async()=>{
   const next=await api<Run>('/v2/kitchen',{session_id:run.session_id,kitchen:equipment,revision:run.revision,preferences_version:run.preferences_version},'PUT');
   setStoredKitchen(equipment);applyState(next);kitchenDialog.current?.close();
   if(next.ready)applyState(await askAgent('/v2/turn',{action:'schedule',revision:next.revision,preferences_version:next.preferences_version}));
  })
 }
 async function mark(t:Task,done:boolean){
  if(!run?.timeline)return;
  const body={session_id:run.session_id,timeline_id:run.timeline.id,step_id:t.id,done,revision:run.revision,preferences_version:run.preferences_version};
  await action(async()=>applyState(await api<Run>('/v2/progress',body,'PUT')));
 }
 function sessionList(){
  return <><button className="new-session" disabled={working||!run} onClick={()=>switchSession()}>＋ 新对话</button>
   <h2>历史对话</h2><nav aria-label="历史对话">{sessions.map(entry=>
    <button key={entry.id} disabled={working||!run} aria-current={entry.id===run?.session_id?'page':undefined}
      onClick={()=>switchSession(entry.id)}>{entry.title}</button>)}</nav></>
 }
 return <div className={sidebarCollapsed?"workspace sidebar-collapsed":"workspace"}><aside id="session-sidebar" className="session-sidebar">{sessionList()}</aside>
 <dialog className="session-drawer" ref={sessionDrawer} aria-label="历史对话">
 <button className="drawer-close" onClick={()=>sessionDrawer.current?.close()}>关闭</button>{sessionList()}</dialog>
 <div className="chat"><header><div><button className="sidebar-toggle" aria-controls="session-sidebar" aria-expanded={!sidebarCollapsed} onClick={toggleSidebar}>{sidebarCollapsed?'展开对话栏':'收起对话栏'}</button><button className="history-toggle" onClick={()=>sessionDrawer.current?.showModal()}>历史对话</button><strong>灶边</strong><small>厨房助手</small></div><nav aria-label="设置">
 <button disabled={working||!run} onClick={openKitchen}>厨房配置</button> <button disabled={working||!saved} onClick={()=>{setPrefs(saved);dialog.current?.showModal()}}>饮食偏好</button></nav></header>
 <main aria-label="对话记录" aria-live="polite"><div className="thread">
 {messages.map((m,i)=><article key={i} className={'bubble '+m.role}><small>{m.role==='user'?'我':'灶边'}</small>{m.image&&<img src={'/api/images/'+m.image} alt="上传的厨房照片"/>}{m.role==='assistant'?<Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{m.content}</Markdown>:<p>{m.content}</p>}</article>)}
 {run?.menu&&<section className="menu-panel" aria-label="当前餐单">
 <h2>{run.menu.people} 人餐单</h2>
 <div className="dish-grid">{run.menu.dishes.map(d=><article className="recipe-card" key={d.id}>
 <small>{roles[d.course]??d.course} · {d.portions} 标准份</small><h3>{d.name}</h3>
 <p>{d.ingredients.map(i=>i.name+' '+i.amount+(units[i.unit]??i.unit)).join('、')}</p>
 <button disabled={busy} aria-expanded={preview?.dish_id===d.id} onClick={()=>openAlternatives(d)}>{preview?.dish_id===d.id?'收起候选':'换这道'}</button>
 {preview?.dish_id===d.id&&<div className="swap-panel" ref={panel} aria-label={d.name+'的替代菜'}>
 <h4>挑一道替代菜</h4>
 <form onSubmit={e=>{e.preventDefault();if(swapRequest.trim()&&!busy)menuAction('replace',d)}}>
 <input aria-label="本次换菜要求" placeholder="例如：想用牛肉、不用炒锅" maxLength={500} value={swapRequest} onChange={e=>setSwapRequest(e.target.value)}/>
 <button disabled={busy||!swapRequest.trim()}>筛选</button></form>
 {!preview.options.length&&<p>没有符合这次要求的替代菜。可以调整条件，或保留原菜。</p>}
 {run.timeline&&<p>换菜后需要重新安排烹饪时间线。</p>}
 {preview.options.map(option=><section className="swap-option" key={option.id}>
 <strong>{option.name}</strong><small>约 {option.minutes} 分钟</small>
 <p>{option.reason}{option.equipment.length?' · '+option.equipment.map(e=>kitchenLabels[e as keyof Kitchen]).join('、'):''}</p>
 <p>{option.added.length?'多买 '+quantities(option.added):option.needs_shopping?'无需额外添购':'整餐无需添购'}{option.saved.length?'；少买 '+quantities(option.saved):''}</p>
 <button disabled={busy} aria-label={'换成'+option.name} onClick={()=>menuAction('choose',d,option)}>换成这道</button></section>)}
 </div>}</article>)}</div>
 {run.undo_swap&&<div className="undo-swap">已换成{run.menu.dishes.find(d=>!run.undo_swap!.menu.dishes.some(old=>old.id===d.id))?.name} <button disabled={busy} onClick={()=>menuAction('undo')}>撤销换菜</button></div>}
 {!run.ready&&<button className="primary" disabled={busy} onClick={()=>menuAction('ready')}>食材已备齐</button>}
 {run.ready&&!run.timeline&&<button disabled={busy} onClick={()=>storedKitchen?menuAction('schedule'):openKitchen()}>{storedKitchen?'生成时间线':'配置厨房并生成时间线'}</button>}
 </section>}
 {run?.timeline&&<section className="menu-panel" aria-label="烹饪时间线">
 <h2>烹饪时间线</h2><p>预计 {run.timeline.minutes} 分钟上桌 · 已完成 {run.timeline.done.length}/{run.timeline.steps.length} 步</p>
 {run.timeline.steps.map(t=>{
  const timeline=run.timeline!;
  const checked=timeline.done.includes(t.id);
  const waiting=t.after.filter(id=>!timeline.done.includes(id));
  const locked=checked&&timeline.steps.some(s=>s.after.includes(t.id)&&timeline.done.includes(s.id));
  const parallel=timeline.steps.filter(s=>!s.hands&&s.dish!==t.dish&&s.start<t.end&&t.start<s.end);
  return <div className="timeline-row" key={t.id}><label><input type="checkbox" checked={checked}
   disabled={busy||locked||waiting.length>0} onChange={e=>mark(t,e.target.checked)}/>
   <b>{t.start}–{t.end} 分</b> {t.dish} · {t.action}</label>
   {waiting.length>0&&<small>先完成：{waiting.map(id=>timeline.steps.find(s=>s.id===id)?.action).join('、')}</small>}
   {!!t.hands&&parallel.length>0&&<small>同时进行：{parallel.map(s=>s.dish+' · '+s.action).join('、')}</small>}
  </div>})}</section>}
 {progress && <div className="bubble assistant tool-progress" role="status">
  {progress.length === 0 ? '灶边正在处理…' : <>
   <small>本轮工具</small>
   <ul>{progress.map(tool => <li key={tool.id}>
    <span>{toolLabels[tool.name] ?? tool.name}</span>
    <span className={tool.status}>{toolStatus[tool.status]}</span>
   </li>)}</ul>
   {progress.every(tool => tool.status !== 'running') && <small>灶边正在继续处理…</small>}
  </>}
 </div>}
 <div ref={end}/></div></main>
 <footer><div className="compose">
 {error&&<div role="alert">{error}</div>}
 {file&&<div className="attachment">{file.name} <button onClick={()=>setFile(null)}>移除</button></div>}
 <textarea aria-label="消息" maxLength={12000} placeholder="问问灶边，或上传食材照片…" value={draft} onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/>
 <div className="bar"><input ref={picker} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={e=>{setFile(e.target.files?.[0]??null);e.target.value=''}}/>
 <button disabled={working} onClick={()=>picker.current?.click()}>＋ 图片</button>
 <button className="primary send" disabled={working||(!draft.trim()&&!file)||!saved} onClick={send}>发送 ↑</button></div></div></footer>
 <dialog ref={kitchenDialog} aria-labelledby="kitchen-title"><div className="bar"><h2 id="kitchen-title">厨房配置</h2><button className="close" onClick={()=>kitchenDialog.current?.close()}>关闭</button></div>
 <p>厨房配置在所有对话中共享。填写可同时使用的数量，没有的设备填 0。</p>
 {(Object.keys(kitchenLabels) as (keyof Kitchen)[]).map(key=><label className="equipment" key={key}>{kitchenLabels[key]}<input type="number" min={key==='cooks'?1:0} max={['steamer','rice_cooker','oven'].includes(key)?4:8} value={equipment[key]} onChange={e=>setEquipment({...equipment,[key]:Number(e.target.value)})}/></label>)}
 <button className="primary" disabled={busy} onClick={saveKitchen}>{run?.ready?'保存并生成时间线':'保存厨房配置'}</button>
 {error&&<p role="alert">{error}</p>}</dialog>
 <dialog ref={dialog} aria-labelledby="prefs-title"><div className="bar"><h2 id="prefs-title">饮食偏好</h2><button className="close" onClick={()=>dialog.current?.close()}>关闭</button></div>
 {prefs&&<><label>用餐人数<input type="number" min="1" max="20" value={prefs.servings} onChange={e=>change({servings:Number(e.target.value)})}/></label>
 <h3>过敏信息</h3><div className="chips">{Object.entries(allergyLabels).map(([id,label])=><button className={prefs.allergens.includes(id)?'selected':''} key={id} onClick={()=>change({allergens:toggle(prefs.allergens,id)})}>{label}</button>)}</div>
 <h3>不吃的食材</h3><div className="chips">{Object.entries(catalog).map(([id,v])=><button key={id} className={prefs.excluded_ingredient_ids.includes(id)?'selected':''} onClick={()=>change({excluded_ingredient_ids:toggle(prefs.excluded_ingredient_ids,id)})}>{v.name}</button>)}</div>
 <label className="check"><input type="checkbox" checked={prefs.dietary_rules.includes('vegetarian')} onChange={e=>change({dietary_rules:e.target.checked?['vegetarian']:[]})}/>蛋奶素（不含肉鱼）</label>
 <button disabled={!dirty||busy} onClick={()=>action(save)}>保存偏好{dirty?' · 有修改':''}</button>
 <p className="hint">饮食偏好在所有对话中共享。过敏与忌口会直接排除冲突菜谱。照片无法确认包装成分与交叉接触，请核对标签。</p></>}
 {error&&<p role="alert">{error}</p>}</dialog></div></div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
