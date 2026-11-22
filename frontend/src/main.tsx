import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './style.css';

type Item={ingredient_id:string;amount:number|null;unit:string};
type Pref={version:number;allergens:string[];excluded_ingredient_ids:string[];dietary_rules:string[];servings:number};
type Detail={name:string;required:number;unit:string;missing:number|null;status:string};
type Recipe={id:string;name:string;score:number;time_minutes:number;coverage:number;adequacy:number;details:Detail[];steps:string[]};
type Run={id:string;status:string;preferences_version:number;items:Item[];extra_foods?:string[];caption_question?:string;vision_provider?:string;warning?:string;uncertain?:string[];error?:string;stale?:boolean;events:{tool:string;ok:boolean}[];result?:{recipes:Recipe[];excluded:{id:string;reason:string}[]}};
async function api<T>(path:string, body?:unknown, method?:string):Promise<T>{
 const res=await fetch('/api'+path,{method:method??(body?'POST':'GET'),headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 const data=await res.json(); if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查后重试'); return data;
}
const allergyLabels:Record<string,string>={egg:'蛋类',soy:'大豆',wheat:'小麦',milk:'乳制品',peanut:'花生',fish:'鱼类',shellfish:'甲壳贝类',sesame:'芝麻'};
const units:Record<string,string>={g:'克',piece:'个',ml:'毫升'};
type Message={meal?:Run;role:'assistant'|'user';content:string;image?:string};
const welcome:Message={role:'assistant',content:'今天想做什么菜？可以告诉我手边有哪些食材，或发一张厨房台面的照片。'};
type Nutrition={kcal:number;protein:number;fat:number;carbs:number};
type Lists={shopping:Detail[];nutrition:{total:Nutrition;per_person:Nutrition;unavailable:string[];servings:number}};
function nutritionText(n:Nutrition){
 return n.kcal+' kcal，蛋白质 '+n.protein+' g，脂肪 '+n.fat+' g，碳水 '+n.carbs+' g';
}
function App(){
 const [catalog,setCatalog]=useState<Record<string,{name:string}>>({});
 const [prefs,setPrefs]=useState<Pref|null>(null),[saved,setSaved]=useState<Pref|null>(null);
 const [messages,setMessages]=useState<Message[]>([welcome]),[draft,setDraft]=useState('');
 const [file,setFile]=useState<File|null>(null),[run,setRun]=useState<Run|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),end=useRef<HTMLDivElement>(null),picker=useRef<HTMLInputElement>(null);
 const dirty=JSON.stringify(prefs)!==JSON.stringify(saved);
 const append=(m:Message)=>setMessages(previous=>[...previous,m]);
 const working=busy;
 useEffect(()=>{Promise.all([api<typeof catalog>('/ingredients'),api<Pref>('/preferences')]).then(([c,p])=>{setCatalog(c);setSaved(p);setPrefs(p)}).catch(e=>setError(e.message))},[]);
 useEffect(()=>{end.current?.scrollIntoView({block:'end'})},[messages,run?.status,busy]);
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
   const next=await api<Run & {chat?:boolean}>('/meal-turn',{meal_id:run?.id,image_id,text:content,preferences_version:saved.version});
   let responseContext='';
   if(!next.chat){
    setRun(next);
    const inventory=next.items.map(i=>(catalog[i.ingredient_id]?.name??i.ingredient_id)+(i.amount==null?'':' '+i.amount+units[i.unit])).concat(next.extra_foods??[]);
    const summary=(next.vision_provider==='mock'?'模拟识别。':'')+(inventory.length?'当前食材：'+inventory.join('、')+'。':'');
    responseContext=[summary,...(next.uncertain??[]),...(next.result?.recipes??[]).map(r=>r.name)].join(' ');
    append({role:'assistant',content:[summary,...(next.uncertain??[]),next.status==='completed'?(next.result?.recipes.length?'想做哪一道？':'没有符合当前饮食限制的菜谱。'):''].filter(Boolean).join('\n\n'),meal:next});
   }
   if(next.chat||next.caption_question){
    const turns=[...messages,message];
    if(responseContext)turns.push({role:'assistant',content:responseContext},{role:'user',content});
    const history=turns.slice(-20).map(({role,content,meal})=>({role,content:(content+(meal?.result?.recipes.map(r=>r.name).join('、')??'')).slice(0,12000)}));
    const reply=await api<{content:string}>('/chat',{messages:history,preferences_version:saved.version});
    append({role:'assistant',content:reply.content});
   }
  })
 }
 async function selectRecipe(meal:Run,recipe:Recipe){
  await action(async()=>{
   const lists=await api<Lists>('/meals/'+meal.id+'/select',{recipe_id:recipe.id,preferences_version:meal.preferences_version});
   append({role:'user',content:'选 '+recipe.name});
   const shopping=lists.shopping.map(d=>'- '+d.name+'：'+(d.missing===null?'已有，余量未知；需要 '+d.required+units[d.unit]:d.missing+units[d.unit])).join('\n')||'库存已足够。';
   const n=lists.nutrition;
   append({role:'assistant',content:['### '+recipe.name,recipe.steps.map((v,i)=>(i+1)+'. '+v).join('\n'),'### 待买清单',shopping,'### 营养估算',n.servings+' 人份合计：'+nutritionText(n.total),'每人：'+nutritionText(n.per_person),n.unavailable.length?'未计入：'+n.unavailable.join('、'):'','按菜谱用量及 USDA SR Legacy 参考食材计算。'].filter(Boolean).join('\n\n')});
  });
 }
 return <div className="chat"><header><div><strong>灶边</strong><small>厨房助手</small></div><button disabled={working||!saved} onClick={()=>{setPrefs(saved);dialog.current?.showModal()}}>饮食偏好</button></header>
 <main aria-label="对话记录" aria-live="polite"><div className="thread">
 {messages.map((m,i)=><article key={i} className={'bubble '+m.role}><small>{m.role==='user'?'我':'灶边'}</small>{m.image&&<img src={m.image} alt="上传的厨房照片"/>}{m.role==='assistant'?<Markdown remarkPlugins={[remarkGfm]} components={{img:()=>null}}>{m.content}</Markdown>:<p>{m.content}</p>}{m.meal?.result?.recipes.map(r=><section className="recipe-card" key={r.id}><h3>{r.name}</h3><p>{r.time_minutes} 分钟 · 匹配 {r.score} 分</p><p>{r.details.map(d=>d.name+' '+d.required+units[d.unit]).join('、')}</p><button disabled={busy||m.meal?.id!==run?.id} onClick={()=>selectRecipe(m.meal!,r)}>选这道菜</button></section>)}</article>)}
 {working&&<p role="status">灶边正在处理…</p>}
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
