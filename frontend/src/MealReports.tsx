type Nutrient = 'kcal' | 'protein' | 'fat' | 'carbs';

export type Shopping = { buy: { ingredient_id: string; name: string }[] };
export type Nutrition = {
 per_person: Record<Nutrient, number>;
 unavailable: string[];
 servings: number;
};

const metrics: { key: Nutrient; label: string; unit: string }[] = [
 {key:'kcal',label:'能量',unit:'千卡'},
 {key:'protein',label:'蛋白质',unit:'克'},
 {key:'fat',label:'脂肪',unit:'克'},
 {key:'carbs',label:'碳水',unit:'克'},
];

export default function MealReports({shopping,nutrition}: {shopping?:Shopping|null; nutrition?:Nutrition|null}) {
 const names = [...new Set((shopping?.buy ?? []).map(item=>item.name))];
 const unavailable = [...new Set(nutrition?.unavailable ?? [])];
 return <div className="report-grid">
  <section className="surface report-card">
   <h2>待买清单</h2>
   {shopping ? names.length ? <ul className="shopping-list">
    {names.map(name=><li key={name}>{name}</li>)}
   </ul> : <p className="report-empty">手边的食材够用。</p> : <p className="report-empty">可重新生成采购与营养。</p>}
  </section>
  <section className="surface report-card">
   <h2>每人营养估算</h2>
   {nutrition ? <>
    <dl className="metric-grid">{metrics.map(({key,label,unit})=><div key={key}>
     <dt>{label}</dt><dd>{Math.round(nutrition.per_person[key])} <small>{unit}</small></dd>
    </div>)}</dl>
    {!!unavailable.length&&<p className="hint">未计入{unavailable.join('、')}。</p>}
   </> : <p className="report-empty">还没有营养估算。</p>}
  </section>
 </div>;
}
