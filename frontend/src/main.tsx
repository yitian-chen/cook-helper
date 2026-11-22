import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './style.css';

type Item={ingredient_id:string;amount:number|null;unit:string};
type Pref={version:number;allergens:string[];excluded_ingredient_ids:string[];dietary_rules:string[];servings:number};
type Detail={name:string;required:number;unit:string;missing:number|null;status:string};
type Recipe={id:string;name:string;score:number;time_minutes:number;coverage:number;adequacy:number;details:Detail[];steps:string[]};
type Run={id:string;status:string;preferences_version:number;items:Item[];warning?:string;uncertain?:string[];error?:string;stale?:boolean;events:{tool:string;ok:boolean}[];result?:{recipes:Recipe[];excluded:{id:string;reason:string}[]}};
async function api<T>(path:string, body?:unknown, method?:string):Promise<T>{
 const res=await fetch('/api'+path,{method:method??(body?'POST':'GET'),headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 const data=await res.json(); if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查后重试'); return data;
}
const allergyLabels:Record<string,string>={egg:'蛋类',soy:'大豆',wheat:'小麦',milk:'乳制品',peanut:'花生',fish:'鱼类',shellfish:'甲壳贝类',sesame:'芝麻'};
const units:Record<string,string>={g:'克',piece:'个',ml:'毫升'};
type Message={role:'assistant'|'user';content:string;image?:string};
const welcome:Message={role:'assistant',content:'今天想做什么菜？可以告诉我手边有哪些食材，或发一张厨房台面的照片。'};
function recipeReply(run:Run){
 const recipes=run.result?.recipes??[];
 if(!recipes.length)return '没有找到符合当前饮食限制的菜谱。';
 return recipes.map(r=>[
  '### '+r.name, r.time_minutes+' 分钟 · 匹配 '+r.score+' 分（种类覆盖 '+Math.round(r.coverage*100)+'%，数量满足 '+Math.round(r.adequacy*100)+'%）',
  r.details.map(d=>'- '+d.name+' '+d.required+units[d.unit]+' · '+(d.status==='enough'?'已足量':d.status==='unknown'?'数量待确认':'缺 '+d.missing+units[d.unit])).join('\n'),
  r.steps.map((step,i)=>(i+1)+'. '+step).join('\n')
 ].join('\n\n')).join('\n\n---\n\n');
}
function App(){
 const [catalog,setCatalog]=useState<Record<string,{name:string}>>({});
 const [prefs,setPrefs]=useState<Pref|null>(null),[saved,setSaved]=useState<Pref|null>(null);
 const [messages,setMessages]=useState<Message[]>([welcome]),[draft,setDraft]=useState('');
 const [file,setFile]=useState<File|null>(null),[run,setRun]=useState<Run|null>(null),[items,setItems]=useState<Item[]>([]);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),end=useRef<HTMLDivElement>(null),picker=useRef<HTMLInputElement>(null);
 const caption=useRef<{runId:string;history:Message[];question:string}|null>(null);
 const dirty=JSON.stringify(prefs)!==JSON.stringify(saved);
 const append=(m:Message)=>setMessages(previous=>[...previous,m]);
 const working=busy||!!run&&['queued','running'].includes(run.status);
 useEffect(()=>{Promise.all([api<typeof catalog>('/ingredients'),api<Pref>('/preferences')]).then(([c,p])=>{setCatalog(c);setSaved(p);setPrefs(p)}).catch(e=>setError(e.message))},[]);
 useEffect(()=>{end.current?.scrollIntoView({block:'end'})},[messages,run?.status,busy]);
 useEffect(()=>{
  if(!run||!['queued','running'].includes(run.status))return;
  let active=true;
  let timer=0;
  async function poll(id:string){
   try{
    const next=await api<Run>('/runs/'+id);
    if(!active)return;
    if(['queued','running'].includes(next.status)){timer=window.setTimeout(()=>poll(id),800);return}
    if(next.stale){setRun(null);append({role:'assistant',content:'饮食偏好已更新，请重新录入食材。'});return}
    setRun(next);
    if(next.status==='awaiting_confirmation'){
     setItems(next.items);
     const names=next.items.map(i=>catalog[i.ingredient_id]?.name+' '+(i.amount??'数量未知')+(i.amount===null?'':units[i.unit]));
     const recognition=[next.warning,names.join('、'),...(next.uncertain??[])].filter(Boolean).join('\n\n');
     append({role:'assistant',content:recognition});
     const pending=caption.current;
     if(pending?.runId===id){
      caption.current=null;
      setBusy(true);
      try{
       const context=[...pending.history,{role:'assistant',content:recognition},{role:'user',content:pending.question}];
       const reply=await api<{content:string}>('/chat',{messages:context.slice(-20).map(({role,content})=>({role,content:content.slice(0,12000)})),preferences_version:next.preferences_version});
       append({role:'assistant',content:reply.content});
      }catch(e){setError((e as Error).message)}finally{setBusy(false)}
     }
    }else append({role:'assistant',content:next.status==='completed'?recipeReply(next):next.error??'任务已取消。'});
   }catch(e){if(active){setError((e as Error).message);timer=window.setTimeout(()=>poll(id),2500)}}
  }
  poll(run.id);return()=>{active=false;clearTimeout(timer)};
 },[run?.id,run?.status,catalog]);
 async function action(fn:()=>Promise<void>){setError('');setBusy(true);try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function save(){
  if(!prefs)return;
  const {version,...body}=prefs;
  const updated=await api<Pref>('/preferences',body,'PUT');
  setSaved(updated);setPrefs(updated);setRun(null);setMessages([{role:'assistant',content:'已保存饮食偏好。接下来的对话会使用新设置。'}]);dialog.current?.close();
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
   if(image_id){
    const next=await api<Run>('/runs',{image_id});
    caption.current=content?{runId:next.id,history:[...messages,message],question:content}:null;
    setRun(next);setItems([]);
   }
   if(content&&!image_id){
    const history=[...messages,message].slice(-20).map(({role,content})=>({role,content:content.slice(0,12000)}));
    const reply=await api<{content:string}>('/chat',{messages:history,preferences_version:saved.version});
    append({role:'assistant',content:reply.content});
   }
  })
 }
 return <div className="chat"><header><div><strong>灶边</strong><small>厨房助手</small></div><button disabled={working||!saved} onClick={()=>{setPrefs(saved);dialog.current?.showModal()}}>饮食偏好</button></header>
 <main aria-label="对话记录" aria-live="polite"><div className="thread">
 {messages.map((m,i)=><article key={i} className={'bubble '+m.role}><small>{m.role==='user'?'我':'灶边'}</small>{m.image&&<img src={m.image} alt="上传的厨房照片"/>}{m.role==='assistant'?<Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{m.content}</Markdown>:<p>{m.content}</p>}</article>)}
 <section className="inventory">
 {run?.status==='awaiting_confirmation'&&<><h3>核对食材与数量</h3><p className="hint">数量未知可留空。个数不自动换算成克数，避免虚构重量。</p>
 {items.map((item,index)=><div className="item" key={index}><select aria-label="食材" value={item.ingredient_id} onChange={e=>setItems(items.map((v,i)=>i===index?{...v,ingredient_id:e.target.value}:v))}>{Object.entries(catalog).map(([id,v])=><option key={id} value={id}>{v.name}</option>)}</select>
 <input aria-label="数量" type="number" min="0" placeholder="未知" value={item.amount??''} onChange={e=>setItems(items.map((v,i)=>i===index?{...v,amount:e.target.value===''?null:Number(e.target.value)}:v))}/>
 <select aria-label="单位" value={item.unit} onChange={e=>setItems(items.map((v,i)=>i===index?{...v,unit:e.target.value}:v))}>{Object.entries(units).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select>
 <button aria-label="移除食材" onClick={()=>setItems(items.filter((_,i)=>i!==index))}>×</button></div>)}
 <div className="actions"><button onClick={()=>{const id=Object.keys(catalog).find(k=>!items.some(i=>i.ingredient_id===k));if(id)setItems([...items,{ingredient_id:id,amount:null,unit:'g'}])}}>＋ 添加食材</button>
 <button className="primary" disabled={busy||dirty} onClick={()=>action(async()=>setRun(await api<Run>('/runs/'+run.id+'/confirm-inventory',{items,preferences_version:run.preferences_version})))}>确认，匹配菜谱 →</button></div>{dirty&&<p className="hint">偏好已修改，请保存并重新开始。</p>}</>}
 </section>
 {run&&['queued','running','awaiting_confirmation'].includes(run.status)&&<button className="subtle" onClick={()=>action(async()=>{setRun(await api<Run>('/runs/'+run.id+'/cancel',{}));append({role:'assistant',content:'已取消食材任务。'})})}>取消食材任务</button>}
 {working&&<p role="status">灶边正在处理…</p>}
 <div ref={end}/></div></main>
 <footer><div className="compose">
 {error&&<div role="alert">{error}</div>}
 {file&&<div className="attachment">{file.name} <button onClick={()=>setFile(null)}>移除</button></div>}
 <textarea aria-label="消息" maxLength={12000} placeholder="问问灶边，或上传食材照片…" value={draft} onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/>
 <div className="bar"><input ref={picker} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={e=>{setFile(e.target.files?.[0]??null);e.target.value=''}}/>
 <button disabled={working||run?.status==='awaiting_confirmation'} onClick={()=>picker.current?.click()}>＋ 图片</button>
 <button disabled={working||run?.status==='awaiting_confirmation'||!saved} onClick={()=>action(async()=>{const next=await api<Run>('/runs',{});setItems([]);setRun(next);append({role:'user',content:'手动录入食材'})})}>录入食材</button>
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
