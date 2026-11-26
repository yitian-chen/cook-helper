import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CookingFlow, {type Task} from './CookingFlow';
import MealInput from './MealInput';
import MealReports, {type Nutrition,type Shopping} from './MealReports';
import WorkspaceIcon from './WorkspaceIcon';
import './style.css';

type Pref={version:number;allergens:string[];excluded_ingredient_ids:string[];dietary_rules:string[];servings:number};
type Ingredient={ingredient_id:string;name:string;amount:number|null;unit:string};
type Dish={id:string;name:string;course:string;portions:number;ingredients:Ingredient[]};
type Kitchen={cooks:number;burner:number;wok:number;pot:number;steamer:number;rice_cooker:number;oven:number};
type Message={role:'user'|'assistant';content:string;image?:string};
type Session={id:string;title:string};
const currentRun=(id:string)=>api<Run>('/v2/current?session_id='+encodeURIComponent(id));
type Quantity={name:string;unit:string;amount:number};
type Alternative={id:string;name:string;reason:string;minutes:number;added:Quantity[];saved:Quantity[];needs_shopping:boolean;equipment:string[]};
type Preview={dish_id:string;options:Alternative[]};
type Run={items:Ingredient[];extra_foods:string[];questions:string[];shopping?:Shopping|null;nutrition?:Nutrition|null;swap_preview?:Preview|null;undo_swap?:{menu:{dishes:Dish[]}}|null;session_id:string;revision:number;preferences_version:number;messages:Message[];confirmed?:boolean;ready:boolean;
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
 compose_menu:'搭配整餐',replace_dish:'比较替代菜',choose_dish:'更新餐单',undo_swap:'恢复原菜',confirm_menu:'确定餐单',confirm_ready:'确认备餐',generate_timeline:'编排烹饪流程',
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
const roles:Record<string,string>={main:'主菜',side:'配菜',soup:'汤',staple:'主食'};
const kitchenLabels:Record<keyof Kitchen,string>={cooks:'下厨人数',burner:'灶眼',wok:'炒锅',pot:'汤锅',steamer:'蒸锅',rice_cooker:'电饭锅',oven:'烤箱'};
const initialKitchen:Kitchen={cooks:1,burner:1,wok:1,pot:1,steamer:0,rice_cooker:1,oven:0};

const stages = [
 {title:'上传食材图片',hint:'也可用语音补充'},
 {title:'选定菜谱',hint:'挑出想做的菜'},
 {title:'补充食材',hint:'核对采购与营养'},
 {title:'烹饪',hint:'跟着流程做'},
] as const;
type Stage = 0|1|2|3;
const stageFor=(run:Run|null):Stage=>
 run?.ready?3:run?.confirmed?2:run?.menu?1:0;
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
 const [draft,setDraft]=useState('');
 const [stageOverride,setStageOverride]=useState<Stage|null>(null);
 const [notice,setNotice]=useState('');
 const [file,setFile]=useState<File|null>(null),[run,setRun]=useState<Run|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const [preview,setPreview]=useState<Preview|null>(null),[swapRequest,setSwapRequest]=useState('');
 const panel=useRef<HTMLDivElement>(null);
 const [progress,setProgress]=useState<ToolProgress[]|null>(null);
 const dialog=useRef<HTMLDialogElement>(null),picker=useRef<HTMLInputElement>(null),main=useRef<HTMLElement>(null);
 const kitchenDialog=useRef<HTMLDialogElement>(null);
 const [equipment,setEquipment]=useState<Kitchen>(initialKitchen);
 const [storedKitchen,setStoredKitchen]=useState<Kitchen|null>(null);
 function applyState(next:Run){
  setPreview(next.swap_preview??null);setSwapRequest('');
  setRun(next);
 }
 function openKitchen(){setEquipment(storedKitchen??initialKitchen);kitchenDialog.current?.showModal()}
 const dirty=JSON.stringify(prefs)!==JSON.stringify(saved);
 const activeStage=stageFor(run);
 const stage=stageOverride??activeStage;
 const working=busy;
 async function loadSession(id:string){
  const [state,p]=await Promise.all([currentRun(id),api<Pref>('/preferences')]);
  setSaved(p);setPrefs(p);setStoredKitchen(state.kitchen??null);applyState(state);
  localStorage.setItem('cook-helper.session',id);
  setDraft('');setFile(null);setProgress(null);setStageOverride(null);setNotice('');
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
 useEffect(()=>{main.current?.scrollTo(0,0)},[stage,run?.session_id]);
 useEffect(()=>{if(preview)panel.current?.scrollIntoView({block:'nearest'})},[preview]);
 async function action(fn:()=>Promise<void>){setError('');setNotice('');setBusy(true);try{await fn()}catch(e){setError((e as Error).message);try{const [state,p]=await Promise.all([currentRun(run!.session_id),api<Pref>('/preferences')]);applyState(state);setStoredKitchen(state.kitchen??null);setSaved(p);setPrefs(p)}catch{/* keep the original error */}}finally{setBusy(false)}}
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
   setProgress(current=>current?.map(tool=>tool.status==='running'?{...tool,status:'failed'}:tool)??null);
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
   const next=await askAgent('/v2/turn',{revision:run?.revision??0,image_id,text:content,preferences_version:saved.version});
   setDraft('');setFile(null);setStageOverride(next.swap_preview?1:null);applyState(next);
   const inventoryChanged=JSON.stringify([next.items,next.extra_foods])!==JSON.stringify([run?.items,run?.extra_foods]);
   if(!next.questions?.length && stageFor(next)===activeStage && !image_id && !inventoryChanged
    && next.menu?.version===run?.menu?.version && next.timeline?.id===run?.timeline?.id && !next.swap_preview) {
    setNotice(next.messages.at(-1)?.content??'');
   }
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
 async function menuAction(kind:'replace'|'choose'|'undo'|'confirm'|'ready'|'schedule',dish?:Dish,target?:Alternative){
  if(!run)return;
  await action(async()=>{
   const next=await askAgent('/v2/turn',{...version(),action:kind,dish_id:dish?.id,target_id:target?.id,
    text:kind==='replace'?swapRequest.trim():undefined});
   applyState(next);
   if(kind==='confirm'||kind==='ready'||kind==='schedule')setStageOverride(null);
   if(kind==='ready'&&next.ready&&!next.timeline&&!storedKitchen)openKitchen();
  });
 }
 const quantities=(rows:Quantity[])=>rows.map(q=>q.name).join('、');
 async function saveKitchen(){
  if(!run)return;
  await action(async()=>{
   const next=await api<Run>('/v2/kitchen',{session_id:run.session_id,kitchen:equipment,revision:run.revision,preferences_version:run.preferences_version},'PUT');
   setStoredKitchen(equipment);applyState(next);kitchenDialog.current?.close();
   if(next.ready){
    applyState(await askAgent('/v2/turn',{action:'schedule',revision:next.revision,preferences_version:next.preferences_version}));
    setStageOverride(null);
   }
  })
 }
 async function mark(t:Task,done:boolean){
  if(!run?.timeline)return;
  const body={session_id:run.session_id,timeline_id:run.timeline.id,step_id:t.id,done,revision:run.revision,preferences_version:run.preferences_version};
  await action(async()=>applyState(await api<Run>('/v2/progress',body,'PUT')));
 }
 const needsClarification=!!run?.questions?.length;
 const latestResponse=run?.messages.at(-1)?.content;
 function sessionList(){
  return <div className="session-list">
   <button className="new-session" disabled={working||!run} onClick={()=>switchSession()}><WorkspaceIcon name="plus"/>新一餐</button>
   <h2>历史餐单</h2>
   <nav aria-label="历史餐单">{sessions.map(entry=>
    <button key={entry.id} disabled={working||!run} aria-current={entry.id===run?.session_id?'page':undefined}
      onClick={()=>switchSession(entry.id)}>{entry.title}</button>)}</nav>
  </div>
 }
 function steps(){
  return <nav className="stage-nav" aria-label="做饭进度"><ol>
   {stages.map(({title,hint},index)=><li key={title}>
    <button className={index===stage?'viewing':''} aria-current={index===activeStage?'step':undefined}
     disabled={busy||index>activeStage} onClick={()=>{setStageOverride(index as Stage);sessionDrawer.current?.close()}}>
     <span className="stage-dot">{index<activeStage?<WorkspaceIcon name="check"/>:index+1}</span>
     <span><strong>{title}</strong><small>{hint}</small></span>
    </button>
   </li>)}
  </ol></nav>
 }
 const brand=<div className="brand"><span><WorkspaceIcon name="logo"/></span><strong>灶边</strong></div>;
 return <div className={sidebarCollapsed?"workspace sidebar-collapsed":"workspace"}>
  <aside id="session-sidebar" className="session-sidebar">{brand}{steps()}{sessionList()}</aside>
  <dialog className="session-drawer" ref={sessionDrawer} aria-label="做饭进度和历史餐单">
   <div className="bar">{brand}<button className="close" onClick={()=>sessionDrawer.current?.close()}>关闭</button></div>
   {steps()}{sessionList()}
  </dialog>
  <div className="work-area">
   <header className="app-header">
    <div className="header-start">
     <button className="icon-button sidebar-toggle" title={sidebarCollapsed?'展开进度栏':'收起进度栏'} aria-label={sidebarCollapsed?'展开进度栏':'收起进度栏'} aria-controls="session-sidebar" aria-expanded={!sidebarCollapsed} onClick={toggleSidebar}><WorkspaceIcon name="sidebar"/></button>
     <button className="icon-button history-toggle" aria-label="打开进度栏" onClick={()=>sessionDrawer.current?.showModal()}><WorkspaceIcon name="sidebar"/></button>
     <span>烹饪工作台</span>
    </div>
    <nav aria-label="设置">
     <button disabled={working||!run} onClick={openKitchen}><WorkspaceIcon name="kitchen"/>厨房配置</button>
     <button disabled={working||!saved} onClick={()=>{setPrefs(saved);dialog.current?.showModal()}}><WorkspaceIcon name="settings"/>饮食偏好</button>
    </nav>
   </header>
   <main ref={main} aria-label="当前烹饪步骤">
    <div className="stage-content">
     <div className="stage-heading">
      <p className="step-label">步骤 {stage+1} <span>/ 4</span></p>
      <h1>{stage===0?'今天用什么食材？':stages[stage].title}</h1>
      <p>{[
       '拍一张手边食材的照片，也可以用文字或语音补充。',
       '查看推荐的搭配，换掉不合口味的菜，再确认整套餐单。',
       '看一眼需要采购的食材。备齐后，就可以开始安排下厨。',
       '照着并行流程安排准备与下锅，完成后逐项勾选。',
      ][stage]}</p>
     </div>
     {needsClarification&&<section className="surface action-notice" role="status">
      <h2>请确认这些食材</h2><ul>{run.questions.map(question=><li key={question}>{question}</li>)}</ul>
     </section>}
     {notice&&<section className="surface action-notice" aria-label="本次结果"><Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{notice}</Markdown></section>}
     {stage===0&&<>
      <section className="upload-card surface">
       <div className="upload-info">
        <span className="feature-icon"><WorkspaceIcon name="photo"/></span>
        <h2>上传食材图片</h2>
        <p>把食材摆开，尽量拍清楚。想到漏掉的食材，可以一起补充。</p>
        <input ref={picker} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={e=>{setFile(e.target.files?.[0]??null);e.target.value=''}}/>
        <button className="primary" disabled={working||!run} onClick={()=>picker.current?.click()}>选择照片 <WorkspaceIcon name="arrow"/></button>
       </div>
       <div className="upload-image"><img src="/ingredients-example.jpg" alt="番茄、鸡蛋和蔬菜分开摆放的示例"/><span>拍摄示例</span></div>
      </section>
      {!!(run?.items?.length||run?.extra_foods?.length)&&<section className="surface inventory-card">
       <h2>已记录食材</h2><ul>{[...new Set([
        ...(run?.items??[]).filter(item=>item.amount!==0).map(item=>catalog[item.ingredient_id]?.name??item.ingredient_id),
        ...(run?.extra_foods??[]),
       ])].map(name=><li key={name}>{name}</li>)}</ul>
       {run?.menu&&<button onClick={()=>setStageOverride(1)}>查看菜谱 <WorkspaceIcon name="arrow"/></button>}
      </section>}
     </>}
     {stage===1&&run?.menu&&<section className="menu-panel" aria-label="当前餐单">
      <div className="panel-heading"><h2>推荐搭配</h2><span>{run.menu.people} 人份 · {run.menu.dishes.length} 道</span></div>
 <div className="dish-grid">{run.menu.dishes.map(d=><article className="recipe-card" key={d.id}>
 <small>{roles[d.course]??d.course} · {d.portions} 标准份</small><h3>{d.name}</h3>
 <button disabled={busy} aria-expanded={preview?.dish_id===d.id} onClick={()=>openAlternatives(d)}>{preview?.dish_id===d.id?'收起候选':'换这道'}</button>
 {preview?.dish_id===d.id&&<div className="swap-panel" ref={panel} aria-label={d.name+'的替代菜'}>
 <h4>挑一道替代菜</h4>
 <form onSubmit={e=>{e.preventDefault();if(swapRequest.trim()&&!busy)menuAction('replace',d)}}>
 <input aria-label="本次换菜要求" placeholder="例如：想用牛肉、不用炒锅" maxLength={500} value={swapRequest} onChange={e=>setSwapRequest(e.target.value)}/>
 <button disabled={busy||!swapRequest.trim()}>筛选</button></form>
 {!preview.options.length&&<p>没有符合这次要求的替代菜。可以调整条件，或保留原菜。</p>}
 {run.timeline&&<p>换菜后需要重新安排烹饪流程。</p>}
 {preview.options.map(option=><section className="swap-option" key={option.id}>
 <strong>{option.name}</strong><small>约 {option.minutes} 分钟</small>
 <p>{option.reason}{option.equipment.length?' · '+option.equipment.map(e=>kitchenLabels[e as keyof Kitchen]).join('、'):''}</p>
 <p>{(run.confirmed??run.ready)&&option.added.length?'多买 '+quantities(option.added):option.added.length?'需要额外采购':option.needs_shopping?'无需额外添购':'整餐无需添购'}{(run.confirmed??run.ready)&&option.saved.length?'；少买 '+quantities(option.saved):''}</p>
 <button disabled={busy} aria-label={'换成'+option.name} onClick={()=>menuAction('choose',d,option)}>换成这道</button></section>)}
 </div>}</article>)}</div>
 {run.undo_swap&&<div className="undo-swap">已换成{run.menu.dishes.find(d=>!run.undo_swap!.menu.dishes.some(old=>old.id===d.id))?.name} <button disabled={busy} onClick={()=>menuAction('undo')}>撤销换菜</button></div>}
      <div className="stage-actions">
       {!(run.confirmed??run.ready)?<button className="primary" disabled={busy||needsClarification} onClick={()=>menuAction('confirm')}>就做这套 <WorkspaceIcon name="arrow"/></button>:
        <button className="primary" onClick={()=>setStageOverride(2)}>查看待买清单 <WorkspaceIcon name="arrow"/></button>}
      </div>
     </section>}
     {stage===2&&run?.menu&&<>
      <MealReports shopping={run.shopping} nutrition={run.nutrition}/>
      <div className="stage-actions">
       <button disabled={busy} onClick={()=>setStageOverride(1)}>查看餐单</button>
       {!run.shopping&&!run.nutrition&&<button disabled={busy||needsClarification} onClick={()=>menuAction('confirm')}>生成采购与营养</button>}
       {run.ready?<button className="primary" onClick={()=>setStageOverride(null)}>查看烹饪流程 <WorkspaceIcon name="arrow"/></button>:
        <button className="primary" disabled={busy||needsClarification} onClick={()=>menuAction('ready')}>食材已备齐 <WorkspaceIcon name="arrow"/></button>}
      </div>
     </>}
     {stage===3&&run?.timeline&&<CookingFlow timeline={run.timeline} busy={busy} error={error} onMark={mark}/>}
     {stage===3&&run?.ready&&!run.timeline&&<section className="surface action-notice">
      <h2>{storedKitchen?'生成烹饪流程':'配置厨房'}</h2>
      {storedKitchen&&latestResponse&&/需要|冲突|缺少/.test(latestResponse)?
       <Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{latestResponse}</Markdown>:
       <p>告诉灶边厨房里有哪些设备，以便安排可以同时进行的步骤。</p>}
      <button className="primary" disabled={busy||needsClarification} onClick={()=>storedKitchen?menuAction('schedule'):openKitchen()}>{storedKitchen?'生成流程':'配置厨房'}</button>
     </section>}
    </div>
   </main>
   <footer className="workspace-footer">
    {error&&<p className="input-error" role="alert">{error}</p>}
    {(busy||!!progress?.length)&&<section className="tool-progress" role="status" aria-label="工具调用">
     <strong>{busy?'正在处理':'工具调用'}</strong>
     {progress?.length?<ul>{progress.map(tool=><li key={tool.id} className={tool.status}>
      <span>{toolLabels[tool.name]??tool.name}</span><small>{toolStatus[tool.status]}</small>
     </li>)}</ul>:<span className="tool-waiting">请稍候…</span>}
    </section>}
    <MealInput draft={draft} onDraft={setDraft} file={file} onFile={setFile} onSend={send}
     busy={busy} disabled={!run||!saved} sessionId={run?.session_id}
     placeholder={['输入手边的食材…','说说想换的菜或想吃什么…','补充刚买到的食材…','输入烹饪问题…'][stage]}/>
   </footer>
  </div>
 <dialog ref={kitchenDialog} aria-labelledby="kitchen-title"><div className="bar"><h2 id="kitchen-title">厨房配置</h2><button className="close" onClick={()=>kitchenDialog.current?.close()}>关闭</button></div>
 <p>填写可同时使用的数量，没有的设备填 0。配置将用于所有餐单。</p>
 {(Object.keys(kitchenLabels) as (keyof Kitchen)[]).map(key=><label className="equipment" key={key}>{kitchenLabels[key]}<input type="number" min={key==='cooks'?1:0} max={['steamer','rice_cooker','oven'].includes(key)?4:8} value={equipment[key]} onChange={e=>setEquipment({...equipment,[key]:Number(e.target.value)})}/></label>)}
 <button className="primary" disabled={busy} onClick={saveKitchen}>{run?.ready?'保存并生成流程':'保存厨房配置'}</button>
 {error&&<p role="alert">{error}</p>}</dialog>
 <dialog ref={dialog} aria-labelledby="prefs-title"><div className="bar"><h2 id="prefs-title">饮食偏好</h2><button className="close" onClick={()=>dialog.current?.close()}>关闭</button></div>
 {prefs&&<><label>用餐人数<input type="number" min="1" max="20" value={prefs.servings} onChange={e=>change({servings:Number(e.target.value)})}/></label>
 <h3>过敏信息</h3><div className="chips">{Object.entries(allergyLabels).map(([id,label])=><button className={prefs.allergens.includes(id)?'selected':''} key={id} onClick={()=>change({allergens:toggle(prefs.allergens,id)})}>{label}</button>)}</div>
 <h3>不吃的食材</h3><div className="chips">{Object.entries(catalog).map(([id,v])=><button key={id} className={prefs.excluded_ingredient_ids.includes(id)?'selected':''} onClick={()=>change({excluded_ingredient_ids:toggle(prefs.excluded_ingredient_ids,id)})}>{v.name}</button>)}</div>
 <label className="check"><input type="checkbox" checked={prefs.dietary_rules.includes('vegetarian')} onChange={e=>change({dietary_rules:e.target.checked?['vegetarian']:[]})}/>蛋奶素（不含肉鱼）</label>
 <button disabled={!dirty||busy} onClick={()=>action(save)}>保存偏好{dirty?' · 有修改':''}</button>
 <p className="hint">饮食偏好在所有餐单中共享。过敏与忌口会直接排除冲突菜谱。照片无法确认包装成分与交叉接触，请核对标签。</p></>}
 {error&&<p role="alert">{error}</p>}</dialog></div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
