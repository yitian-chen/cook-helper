import './cooking-flow.css';

type Task = {
 id: string;
 dish: string;
 action: string;
 start: number;
 end: number;
 after: string[];
 hands: number;
};

export type CookingTimeline = {id: string; minutes: number; steps: Task[]; done: string[]};
type Stage = {tasks: Task[]; start: number; end: number; waiting: boolean};

function makeDishRows(steps: Task[]) {
 const dishes = new Map<string, Task[][]>();
 for (const task of [...steps].sort((a, b) => a.start - b.start || a.end - b.end)) {
  const tracks = dishes.get(task.dish) ?? [];
  const free = tracks.find(track => track.at(-1)!.end <= task.start);
  if (free) {
   free.push(task);
  } else {
   tracks.push([task]);
   dishes.set(task.dish, tracks);
  }
 }
 return [...dishes].map(([dish, tracks]) => ({
  dish,
  tracks: tracks.map(groupStages),
 }));
}

function groupStages(tasks: Task[]) {
 const stages: Stage[] = [];
 for (const task of tasks) {
  const previous = stages.at(-1);
  if (previous && previous.end === task.start && previous.waiting === !task.hands &&
      previous.tasks.length < 3 && task.after.includes(previous.tasks.at(-1)!.id)) {
   previous.tasks.push(task);
   previous.end = task.end;
  } else {
   stages.push({tasks: [task], start: task.start, end: task.end, waiting: !task.hands});
  }
 }
 return stages;
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
 const x = new Map<number, number>([[0, 0]]);
 for (let i = 1; i < times.length; i++) {
  const before = times[i - 1];
  const minute = times[i];
  let position = x.get(before)! + Math.min(72, (minute - before) * 5);
  for (const stage of endings.get(minute) ?? []) {
   position = Math.max(position, x.get(stage.start)! + 124);
  }
  x.set(minute, position);
 }
 return x;
}

function stageLabel(stage: Stage) {
 return stage.tasks.map(task => task.action.split(/[，,；;]/, 1)[0]).join('、');
}

export default function CookingFlow({timeline, busy, onMark}: {
 timeline: CookingTimeline;
 busy: boolean;
 onMark: (stepIds: string[], done: boolean) => void;
}) {
 const rows = makeDishRows(timeline.steps);
 const stages = rows.flatMap(row => row.tracks.flat());
 const x = makeTimeScale(stages, timeline.minutes);
 const chartWidth = x.get(timeline.minutes)!;
 const done = new Set(timeline.done);
 const completedCount = stages.filter(stage => stage.tasks.every(task => done.has(task.id))).length;

 const ticks = [[0, 0]];
 for (const [minute, position] of x) {
  if (position - ticks.at(-1)![1] >= 60 && chartWidth - position >= 72) ticks.push([minute, position]);
 }
 if (timeline.minutes > 0) ticks.push([timeline.minutes, chartWidth]);

 function offset(position: number) {
  return (position / Math.max(chartWidth, 1)) * 100 + '%';
 }

 return <section className="cooking-flow" aria-labelledby="flow-title">
  <div className="cooking-flow-head">
   <h2 id="flow-title">烹饪流程</h2>
   <span>约 {timeline.minutes} 分钟上桌</span>
  </div>
  <div className="cooking-flow-meta">
   <p>时间重叠的操作可并行。点一下方块标记完成，再点撤销。</p>
   <span><i className="flow-dot"/> 动手 <i className="flow-dot waiting"/> 等待</span>
  </div>
  <div className="flow-scroll" tabIndex={0} role="region" aria-label="可横向滚动的烹饪流程">
   <div className="flow-chart" style={{minWidth:'calc(var(--flow-label-width) + ' + chartWidth + 'px)'}}>
    <div className="flow-row flow-header" aria-hidden="true">
     <span className="flow-label">菜谱</span>
     <div className="flow-axis">
      {ticks.map(([minute, position]) => <span key={minute} style={{left:offset(position)}}>{minute} 分</span>)}
     </div>
    </div>
    {rows.map(({dish, tracks}) => <div className="flow-row" key={dish} role="group" aria-label={dish}>
     <div className="flow-label">{dish}</div>
     <div className="flow-dish">
      <div className="flow-guides" aria-hidden="true">
       {ticks.slice(1,-1).map(([minute, position]) => <span key={minute} style={{left:offset(position)}}/>)}
      </div>
      {tracks.map((track,index) => <div className="flow-track" key={index}>
       {track.map(stage => {
        const {tasks, start, end, waiting} = stage;
        const id = tasks[0].id;
        const complete = tasks.every(task => done.has(task.id));
        return <button type="button" key={id}
         className={'flow-node' + (waiting ? ' waiting' : '') + (complete ? ' completed' : '')}
         aria-label={dish + '，' + stageLabel(stage) + '，第 ' + start + ' 到 ' + end + ' 分钟'}
         aria-pressed={complete}
         disabled={busy}
         onClick={() => onMark(tasks.map(task => task.id), !complete)}
         style={{left:'calc(' + offset(x.get(start)!) + ' + 3px)',width:'calc(' + offset(x.get(end)! - x.get(start)!) + ' - 6px)'}}>
         <span className="flow-node-name">{stageLabel(stage)}</span>
         <span className="flow-node-time">{start}–{end} 分{complete && ' · ✓'}</span>
        </button>;
       })}
      </div>)}
     </div>
    </div>)}
   </div>
  </div>
  <div className="flow-total" aria-live="polite">
   <progress aria-label="烹饪进度" value={completedCount} max={stages.length || 1}/>
   <span>已完成 {completedCount}/{stages.length} 项</span>
  </div>
 </section>;
}
