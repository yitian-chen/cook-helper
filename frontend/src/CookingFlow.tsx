import {useEffect, useRef, useState} from 'react';
import './cooking-flow.css';

export type Task = {
 id: string;
 dish: string;
 action: string;
 start: number;
 end: number;
 after: string[];
 hands: number;
};

type Timeline = {id: string; minutes: number; steps: Task[]; done: string[]};
type Stage = {tasks: Task[]; start: number; end: number; lane: number; waiting: boolean};
type Lane = {dish: string; branch: number; tasks: Task[]};

function makeLanes(steps: Task[]) {
 const dishes = new Map<string, Lane[]>();
 for (const task of [...steps].sort((a, b) => a.start - b.start || a.end - b.end)) {
  const dishLanes = dishes.get(task.dish) ?? [];
  const free = dishLanes.find(lane => lane.tasks.at(-1)!.end <= task.start);
  if (free) {
   free.tasks.push(task);
  } else {
   dishLanes.push({dish: task.dish, branch: dishLanes.length, tasks: [task]});
   dishes.set(task.dish, dishLanes);
  }
 }
 return [...dishes.values()].flat();
}

function makeStages(lanes: Lane[]) {
 return lanes.flatMap(({tasks}, lane) => {
  const stages: Stage[] = [];
  for (const task of tasks) {
   const previous = stages.at(-1);
   if (previous && previous.end === task.start && previous.waiting === !task.hands &&
       previous.tasks.length < 3 && task.after.includes(previous.tasks.at(-1)!.id)) {
    previous.tasks.push(task);
    previous.end = task.end;
   } else {
    stages.push({tasks: [task], start: task.start, end: task.end, lane, waiting: !task.hands});
   }
  }
  return stages;
 }).sort((a, b) => a.start - b.start || a.lane - b.lane);
}

// All lanes share the same time coordinates. Stretch dense intervals to fit cards.
function makeTimeScale(stages: Stage[], duration: number) {
 const times = [...new Set([0, duration, ...stages.flatMap(stage => [stage.start, stage.end])])]
  .sort((a, b) => a - b);
 const endings = new Map<number, Stage[]>();
 for (const stage of stages) {
  const group = endings.get(stage.end) ?? [];
  group.push(stage);
  endings.set(stage.end, group);
 }
 const y = new Map<number, number>([[0, 0]]);
 for (let i = 1; i < times.length; i++) {
  const before = times[i - 1];
  const minute = times[i];
  let position = y.get(before)! + Math.min(84, (minute - before) * 2);
  for (const stage of endings.get(minute) ?? []) {
   position = Math.max(position, y.get(stage.start)! + 88);
  }
  y.set(minute, position);
 }
 return y;
}

function stageLabel(stage: Stage) {
 return stage.tasks.map(task => task.action.split(/[，,；;]/, 1)[0]).join('、');
}

export default function CookingFlow({timeline, busy, onMark, error}: {
 timeline: Timeline;
 busy: boolean;
 error?: string;
 onMark: (task: Task, done: boolean) => void;
}) {
 const lanes = makeLanes(timeline.steps);
 const stages = makeStages(lanes);
 const y = makeTimeScale(stages, timeline.minutes);
 const done = new Set(timeline.done);
 const [selected, setSelected] = useState<string | null>(null);
 const details = useRef<HTMLDialogElement>(null);
 const selectedStage = stages.find(stage => stage.tasks[0].id === selected);
 const height = y.get(timeline.minutes)!;
 const ticks = [[0, 0]];
 for (const [minute, position] of y) {
  if (position - ticks.at(-1)![1] >= 66 && height - position >= 45) ticks.push([minute, position]);
 }
 if (timeline.minutes > 0) ticks.push([timeline.minutes, height]);

 useEffect(() => {
  details.current?.close();
  setSelected(null);
 }, [timeline.id]);

 return <section className="cooking-flow" aria-labelledby="flow-title">
  <div className="cooking-flow-head">
   <h2 id="flow-title">烹饪流程</h2>
   <span>约 {timeline.minutes} 分钟上桌</span>
  </div>
  <div className="cooking-flow-meta">
   <p>从上往下做，横向可并行。点击步骤可查看做法和勾选。</p>
   <span><i className="flow-dot"/> 动手 <i className="flow-dot waiting"/> 等待</span>
  </div>
  <div className="flow-scroll" tabIndex={0} role="region" aria-label="可横向滚动的烹饪流程">
   <div className="flow-chart" style={{
    gridTemplateColumns: 'var(--flow-axis-width) repeat(' + lanes.length + ', minmax(var(--flow-lane-width), 1fr))',
    gridTemplateRows: 'auto ' + (height + 6) + 'px',
   }}>
    <span className="flow-axis-heading">分钟</span>
    {lanes.map((lane, index) => <div className="flow-lane-heading" key={index}
      style={{gridColumn: index + 2}}>{lane.dish}{lane.branch ? ' · 分线 ' + (lane.branch + 1) : ''}</div>)}
    <div className="flow-axis" aria-hidden="true">
     {ticks.map(([minute, position]) =>
      <div className="flow-tick" key={minute} style={{top: position}}><span>{minute}</span></div>)}
    </div>
    {lanes.map((lane, index) => <div className="flow-lane" key={index} aria-hidden="true"
      style={{gridColumn: index + 2, height: y.get(lane.tasks.at(-1)!.end)}}><i/></div>)}
    {stages.map(stage => {
     const {tasks, start, end, waiting, lane} = stage;
     const id = tasks[0].id;
     const complete = tasks.every(task => done.has(task.id));
     return <button type="button" key={id}
       className={'flow-node' + (waiting ? ' waiting' : '') + (complete ? ' completed' : '')}
       aria-label={lanes[lane].dish + '，' + stageLabel(stage) + '，第 ' + start + ' 到 ' + end + ' 分钟' + (complete ? '，已完成' : '')}
       aria-haspopup="dialog"
       onClick={() => {
        setSelected(id);
        details.current?.showModal();
       }}
       style={{gridColumn:lane + 2, marginTop:y.get(start), height:y.get(end)! - y.get(start)! - 9}}>
       <span className="flow-node-name">{stageLabel(stage)}</span>
       <span className="flow-node-time">{start}–{end} 分{complete && <span aria-hidden="true"> · ✓</span>}</span>
      </button>;
    })}
   </div>
  </div>
  <div className="flow-total"><progress value={done.size} max={timeline.steps.length || 1}/>
   <span>已完成 {done.size}/{timeline.steps.length} 步</span>
  </div>
  <dialog ref={details} className="flow-detail-dialog" aria-labelledby="flow-detail-title"
    onClose={() => setSelected(null)}>
   {selectedStage && <>
    <div className="flow-detail-head">
     <div><h3 id="flow-detail-title">{lanes[selectedStage.lane].dish}</h3>
      <p>第 {selectedStage.start}–{selectedStage.end} 分钟</p></div>
     <button type="button" onClick={() => details.current?.close()}>关闭</button>
    </div>
    <div className="flow-detail-steps">
     {selectedStage.tasks.map(task => {
      const checked = done.has(task.id);
      const blocked = checked ?
       timeline.steps.some(step => done.has(step.id) && step.after.includes(task.id)) :
       task.after.some(id => !done.has(id));
      return <label key={task.id}>
       <input type="checkbox" checked={checked} disabled={busy || blocked}
         onChange={event => onMark(task, event.target.checked)}/>
       <span>{task.action}</span>
      </label>;
     })}
    </div>
    {error && <p className="flow-detail-error" role="alert">{error}</p>}
   </>}
  </dialog>
 </section>;
}
