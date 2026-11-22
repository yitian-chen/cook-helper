import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './style.css';

type Pref={version:number;allergens:string[];excluded_ingredient_ids:string[];dietary_rules:string[];servings:number};
type Detail={name:string;required:number;unit:string;missing:number|null;status:string};
type Recipe={id:string;name:string;score:number;time_minutes:number;coverage:number;adequacy:number;details:Detail[];steps:string[]};
type Run={card_id?:string;content:string;id:string;status:string;preferences_version:number;result?:{recipes:Recipe[]}|null};
async function api<T>(path:string, body?:unknown, method?:string):Promise<T>{
 const res=await fetch('/api'+path,{method:method??(body?'POST':'GET'),headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 const data=await res.json(); if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查后重试'); return data;
}
type ToolProgress = {id: string; name: string; status: 'running' | 'completed' | 'failed'};
type AgentEvent = ({type: 'tool'} & ToolProgress) | {type: 'result'; result: Run} | {type: 'error'; detail: string};

const toolLabels: Record<string, string> = {
 recognize_ingredients: '识别食材',
 write_inventory: '更新食材库存',
 match_recipes: '匹配菜谱',
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
type Message={meal?:Run;role:'assistant'|'user';content:string;image?:string};
const welcome:Message={role:'assistant',content:'今天想做什么菜？可以告诉我手边有哪些食材，或发一张厨房台面的照片。'};
function App(){
 const [catalog,setCatalog]=useState<Record<string,{name:string}>>({});
 const [prefs,setPrefs]=useState<Pref|null>(null),[saved,setSaved]=useState<Pref|null>(null);
 const [messages,setMessages]=useState<Message[]>([welcome]),[draft,setDraft]=useState('');
 const [file,setFile]=useState<File|null>(null),[run,setRun]=useState<Run|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const [progress,setProgress]=useState<ToolProgress[]|null>(null);
 const dialog=useRef<HTMLDialogElement>(null),end=useRef<HTMLDivElement>(null),picker=useRef<HTMLInputElement>(null);
 const dirty=JSON.stringify(prefs)!==JSON.stringify(saved);
 const append=(m:Message)=>setMessages(previous=>[...previous,m]);
 const working=busy;
 useEffect(()=>{Promise.all([api<typeof catalog>('/ingredients'),api<Pref>('/preferences')]).then(([c,p])=>{setCatalog(c);setSaved(p);setPrefs(p)}).catch(e=>setError(e.message))},[]);
 useEffect(()=>{end.current?.scrollIntoView({block:'end'})},[messages,run?.status,busy,progress]);
 async function action(fn:()=>Promise<void>){setError('');setBusy(true);try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function askAgent(path: string, body: object) {
  setProgress([]);
  try {
   return await agentRequest(path, body, event => setProgress(current => {
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
  setSaved(updated);setPrefs(updated);setRun(null);setMessages([welcome]);dialog.current?.close();
 }
 const change=(patch:Partial<Pref>)=>{if(prefs)setPrefs({...prefs,...patch})};
 const toggle=(list:string[],value:string)=>list.includes(value)?list.filter(x=>x!==value):[...list,value];
 async function send(){
  if(working||(!draft.trim()&&!file)||!saved)return;
  await action(async()=>{
   const content=draft.trim();
   let image_id:string|undefined;
   if(file){const form=new FormData();form.append('file',file);image_id=(await api<{image_id:string}>('/images',form)).image_id}
   const message:Message={role:'user',content:content||'请识别照片中的食材。',image:image_id?'/api/images/'+image_id:undefined};
   append(message);setDraft('');setFile(null);
   const next=await askAgent('/meal-turn',{meal_id:run?.id,image_id,text:content,preferences_version:saved.version});
   setRun(next);
   append({role:'assistant',content:next.content,meal:next});
  })
 }
 async function selectRecipe(meal:Run,recipe:Recipe){
  await action(async()=>{
   append({role:'user',content:'选 '+recipe.name});
   const next=await askAgent('/meals/'+(run?.id??meal.id)+'/select',{recipe_id:recipe.id,preferences_version:meal.preferences_version});
   setRun(next);
   append({role:'assistant',content:next.content});
  });
 }
 return <div className="chat"><header><div><strong>灶边</strong><small>厨房助手</small></div><button disabled={working||!saved} onClick={()=>{setPrefs(saved);dialog.current?.showModal()}}>饮食偏好</button></header>
 <main aria-label="对话记录" aria-live="polite"><div className="thread">
 {messages.map((m,i)=><article key={i} className={'bubble '+m.role}><small>{m.role==='user'?'我':'灶边'}</small>{m.image&&<img src={m.image} alt="上传的厨房照片"/>}{m.role==='assistant'?<Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{m.content}</Markdown>:<p>{m.content}</p>}{m.meal?.result?.recipes.map(r=><section className="recipe-card" key={r.id}><h3>{r.name}</h3><p>{r.time_minutes} 分钟 · 匹配 {r.score} 分</p><p>{r.details.map(d=>d.name+' '+d.required+units[d.unit]).join('、')}</p><button disabled={busy||m.meal?.id!==run?.card_id} onClick={()=>selectRecipe(m.meal!,r)}>选这道菜</button></section>)}</article>)}
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
 <dialog ref={dialog} aria-labelledby="prefs-title"><div className="bar"><h2 id="prefs-title">饮食偏好</h2><button className="close" onClick={()=>dialog.current?.close()}>关闭</button></div>
 {prefs&&<><label>用餐人数<input type="number" min="1" max="20" value={prefs.servings} onChange={e=>change({servings:Number(e.target.value)})}/></label>
 <h3>过敏信息</h3><div className="chips">{Object.entries(allergyLabels).map(([id,label])=><button className={prefs.allergens.includes(id)?'selected':''} key={id} onClick={()=>change({allergens:toggle(prefs.allergens,id)})}>{label}</button>)}</div>
 <h3>不吃的食材</h3><div className="chips">{Object.entries(catalog).map(([id,v])=><button key={id} className={prefs.excluded_ingredient_ids.includes(id)?'selected':''} onClick={()=>change({excluded_ingredient_ids:toggle(prefs.excluded_ingredient_ids,id)})}>{v.name}</button>)}</div>
 <label className="check"><input type="checkbox" checked={prefs.dietary_rules.includes('vegetarian')} onChange={e=>change({dietary_rules:e.target.checked?['vegetarian']:[]})}/>蛋奶素（不含肉鱼）</label>
 <button disabled={!dirty||busy} onClick={()=>action(save)}>保存偏好{dirty?' · 有修改':''}</button>
 <p className="hint">过敏与忌口会直接排除冲突菜谱。照片无法确认包装成分与交叉接触，请核对标签。</p></>}
 {error&&<p role="alert">{error}</p>}</dialog></div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
