import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Item={ingredient_id:string;amount:number|null;unit:string};
type Pref={version:number;allergens:string[];excluded_ingredient_ids:string[];dietary_rules:string[];servings:number};
type Detail={name:string;required:number;unit:string;missing:number|null;status:string};
type Recipe={id:string;name:string;score:number;time_minutes:number;coverage:number;adequacy:number;details:Detail[];steps:string[]};
type Run={id:string;status:string;preferences_version:number;items:Item[];warning?:string;error?:string;stale?:boolean;events:{tool:string;ok:boolean}[];result?:{recipes:Recipe[];excluded:{id:string;reason:string}[]}};
async function api<T>(path:string, body?:unknown, method?:string):Promise<T>{
 const res=await fetch('/api'+path,{method:method??(body?'POST':'GET'),headers:body instanceof FormData?{}:{'Content-Type':'application/json'},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 const data=await res.json(); if(!res.ok)throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查后重试'); return data;
}
const allergyLabels:Record<string,string>={egg:'蛋类',soy:'大豆',wheat:'小麦',milk:'乳制品',peanut:'花生',fish:'鱼类',shellfish:'甲壳贝类',sesame:'芝麻'};
const units:Record<string,string>={g:'克',piece:'个',ml:'毫升'};
function App(){
 const [catalog,setCatalog]=useState<Record<string,{name:string}>>({});
 const [prefs,setPrefs]=useState<Pref|null>(null);
 const [health,setHealth]=useState<{model?:string;reasoning_provider?:string}>({});
 const [run,setRun]=useState<Run|null>(null),[items,setItems]=useState<Item[]>([]);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[preview,setPreview]=useState('');
 const [dirty,setDirty]=useState(false);
 useEffect(()=>{Promise.all([api<Record<string,{name:string}>>('/ingredients'),api<Pref>('/preferences'),api<typeof health>('/health')]).then(([c,p,h])=>{setCatalog(c);setPrefs(p);setHealth(h)}).catch(e=>setError(e.message))},[]);
 useEffect(()=>{
  if(!run||!['queued','running'].includes(run.status))return;
  let active=true;
  const timer=setInterval(()=>api<Run>('/runs/'+run.id).then(r=>{if(active){setRun(r);if(r.status==='awaiting_confirmation')setItems(r.items)}}).catch(e=>{if(active)setError(e.message)}),1200);
  return()=>{active=false;clearInterval(timer)};
 },[run?.id,run?.status]);
 async function action(fn:()=>Promise<void>){setError('');setBusy(true);try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function save(){if(!prefs)return;const {version,...body}=prefs;setPrefs(await api<Pref>('/preferences',body,'PUT'));setDirty(false);setRun(null)}
 async function start(file?:File){await action(async()=>{
  if(dirty)await save();
  let image_id;
  if(file){const form=new FormData();form.append('file',file);image_id=(await api<{image_id:string}>('/images',form)).image_id;setPreview('/api/images/'+image_id)}else setPreview('');
  const r=await api<Run>('/runs',{image_id});setRun(r);setItems(r.items);
 })}
 const working=busy||!!run&&['queued','running'].includes(run.status);
 const change=(patch:Partial<Pref>)=>{if(prefs){setPrefs({...prefs,...patch});setDirty(true)}};
 const toggle=(list:string[],value:string)=>list.includes(value)?list.filter(x=>x!==value):[...list,value];
 return <div className="shell">
 <header><a className="brand" href="/">灶边<span>COOK HELPER</span></a><span className="badge">厨房灵感 · MVP</span></header>
 <section className="hero"><div className="eyebrow">从手边食材，到一餐好饭</div><h1>今天，做点什么？</h1><p>看看厨房里有什么，让每一份食材都有好去处。</p></section>
 <div className="notice">视觉识别为模拟模式：上传后展示固定示例，请手动修正。推理模型：{health.model??'连接中'} · {health.reasoning_provider==='mock'?'离线模拟':'真实 API'}</div>
 {error&&<div className="error" role="alert">{error}</div>}
 <main><aside className="card"><div className="eyebrow">01 / 饮食偏好</div><h2>先了解你的餐桌</h2>
 {prefs&&<><label>用餐人数<input type="number" min="1" max="20" value={prefs.servings} onChange={e=>change({servings:Number(e.target.value)})}/></label>
 <h3>过敏信息</h3><div className="chips">{Object.entries(allergyLabels).map(([id,label])=><button className={prefs.allergens.includes(id)?'selected':''} key={id} onClick={()=>change({allergens:toggle(prefs.allergens,id)})}>{label}</button>)}</div>
 <h3>不吃的食材</h3><div className="chips">{Object.entries(catalog).map(([id,v])=><button key={id} className={prefs.excluded_ingredient_ids.includes(id)?'selected':''} onClick={()=>change({excluded_ingredient_ids:toggle(prefs.excluded_ingredient_ids,id)})}>{v.name}</button>)}</div>
 <label className="check"><input type="checkbox" checked={prefs.dietary_rules.includes('vegetarian')} onChange={e=>change({dietary_rules:e.target.checked?['vegetarian']:[]})}/>蛋奶素（不含肉鱼）</label>
 <button disabled={!dirty||busy} onClick={()=>action(save)}>保存偏好{dirty?' · 有修改':''}</button>
 <p className="hint">过敏与忌口会直接排除冲突菜谱。照片无法确认包装成分与交叉接触，请核对标签。</p></>}
 </aside><section className="workspace">
 <div className="card"><div className="eyebrow">02 / 厨房库存</div><h2>把食材交给我</h2>
 <label className={'upload '+(working?'disabled':'')}>{preview?<img src={preview} alt="上传的厨房照片"/>:<><span className="camera">＋</span><strong>上传厨房台面照片</strong><span>JPG、PNG、WebP · 最大 8 MB</span></>}
 <input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={working} onChange={e=>{const file=e.target.files?.[0];if(file)start(file);e.target.value=''}}/></label>
 <button className="text-button" disabled={working} onClick={()=>start()}>或手动录入食材 →</button>
 {working&&<p role="status" className="status">Agent 正在处理，请稍候…</p>}
 {run?.warning&&<p className="notice">{run.warning}</p>}
 {run?.status==='awaiting_confirmation'&&<><h3>核对食材与数量</h3><p className="hint">数量未知可留空。个数不自动换算成克数，避免虚构重量。</p>
 {items.map((item,index)=><div className="item" key={index}><select aria-label="食材" value={item.ingredient_id} onChange={e=>setItems(items.map((v,i)=>i===index?{...v,ingredient_id:e.target.value}:v))}>{Object.entries(catalog).map(([id,v])=><option key={id} value={id}>{v.name}</option>)}</select>
 <input aria-label="数量" type="number" min="0" placeholder="未知" value={item.amount??''} onChange={e=>setItems(items.map((v,i)=>i===index?{...v,amount:e.target.value===''?null:Number(e.target.value)}:v))}/>
 <select aria-label="单位" value={item.unit} onChange={e=>setItems(items.map((v,i)=>i===index?{...v,unit:e.target.value}:v))}>{Object.entries(units).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select>
 <button aria-label="移除食材" onClick={()=>setItems(items.filter((_,i)=>i!==index))}>×</button></div>)}
 <div className="actions"><button onClick={()=>{const id=Object.keys(catalog).find(k=>!items.some(i=>i.ingredient_id===k));if(id)setItems([...items,{ingredient_id:id,amount:null,unit:'g'}])}}>＋ 添加食材</button>
 <button className="primary" disabled={busy||dirty} onClick={()=>action(async()=>setRun(await api<Run>('/runs/'+run.id+'/confirm-inventory',{items,preferences_version:run.preferences_version})))}>确认，匹配菜谱 →</button></div>{dirty&&<p className="hint">偏好已修改，请保存并重新开始。</p>}</>}
 {run?.error&&<div className="error">{run.error}</div>}
 {run?.status==='cancelled'&&<p>任务已取消，可重新开始。</p>}
 {run&&['queued','running','awaiting_confirmation'].includes(run.status)&&<button className="text-button" onClick={()=>action(async()=>setRun(await api<Run>('/runs/'+run.id+'/cancel',{})))}>取消当前任务</button>}
 {run&&run.events.length>0&&<details><summary>工具执行记录 · {run.events.length}</summary>{run.events.map((e,i)=><p key={i}>{e.tool} · {e.ok?'成功':'参数无效'}</p>)}</details>}
 </div>
 {run?.status==='completed'&&!dirty&&!run.stale&&<div className="results"><div className="eyebrow">03 / 今晚的灵感</div><h2>从这些菜开始</h2>
 {!run.result?.recipes.length&&<div className="card">没有符合当前限制的菜谱，请调整库存或扩充菜谱。</div>}
 {run.result?.recipes.map(r=><article className="card recipe" key={r.id}><div className="recipe-title"><div><span className="hint">{r.time_minutes} 分钟 · {prefs?.servings} 人份</span><h2>{r.name}</h2></div><div className="score">{r.score}<small>匹配分</small></div></div>
 <p className="hint">种类覆盖 {Math.round(r.coverage*100)}% · 数量满足 {Math.round(r.adequacy*100)}%</p>
 <div className="ingredients">{r.details.map(d=><div key={d.name}><span>{d.name} · {d.required}{units[d.unit]}</span><span className={d.status==='enough'?'good':'missing'}>{d.status==='enough'?'已足量':d.status==='unknown'?'数量待确认':'缺 '+d.missing+units[d.unit]}</span></div>)}</div>
 <ol>{r.steps.map(s=><li key={s}>{s}</li>)}</ol></article>)}
 <p className="hint">已排除 {run.result?.excluded.length??0} 道冲突菜谱。分数用于排序，不表示所有食材已备齐。</p></div>}
 </section></main><footer>灶边 COOK HELPER · 待买清单与营养分析将在后续阶段加入</footer></div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
