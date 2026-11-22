"""Schedule dependency chains while reserving cooks, burners and vessels."""
from collections import Counter, defaultdict
from graphlib import TopologicalSorter
from typing import Literal
from uuid import uuid4

from pydantic import Field
import yaml
from .domain import ROOT, Strict
from .menu import CATALOG

Resource = Literal["burner","pot","wok","steamer","rice_cooker","oven"]
LABEL = dict(burner="灶眼",pot="汤锅",wok="炒锅",steamer="蒸锅",rice_cooker="电饭锅",oven="烤箱")

class Kitchen(Strict):
    cooks: int = Field(ge=1,le=8)
    burner: int = Field(ge=0,le=8)
    pot: int = Field(ge=0,le=8)
    wok: int = Field(ge=0,le=8)
    steamer: int = Field(ge=0,le=4)
    rice_cooker: int = Field(ge=0,le=4)
    oven: int = Field(ge=0,le=4)

class Step(Strict):
    id: str
    action: str = Field(min_length=1)
    minutes: int = Field(gt=0)
    hands: Literal[0,1]
    equipment: list[Resource]
    after: list[str]

def load_steps():
    raw=yaml.safe_load((ROOT/"data/cooking_steps.yaml").read_text())
    if set(raw)!=set(CATALOG):
        raise ValueError("烹饪步骤未覆盖全部菜谱")
    for key, rows in raw.items():
        steps=[Step.model_validate(r) for r in rows]
        ids={s.id for s in steps}
        if len(ids)!=len(steps) or any(set(s.after)-ids for s in steps):
            raise ValueError(f"{key} 的步骤 ID 或依赖无效")
        tuple(TopologicalSorter({s.id:s.after for s in steps}).static_order())
    return raw

STEPS=load_steps()

def schedule(menu,kitchen,items):
    capacity=kitchen.model_dump()
    tasks={}
    def add(key,prefix,name,prereqs=()):
        for raw in STEPS[key]:
            node=prefix+"/"+raw["id"]
            after=[prefix+"/"+i for i in raw["after"]] or list(prereqs)
            resources=raw["equipment"]
            action=raw["action"]
            if resources==["rice_cooker"] and not capacity["rice_cooker"]:
                resources=["pot","burner"]
                action={"s2":"加水烧开后转小火","s3":"小火煮饭，按米种和锅具检查水量","s4":"关火加盖焖饭"}.get(raw["id"],action)
            missing=[LABEL[r] for r in resources if not capacity[r]]
            if missing:
                raise ValueError(name+"需要"+"、".join(missing)+"。请修改厨房配置或换菜。")
            tasks[node]={**raw,"id":node,"action":action,"dish":name,"equipment":resources,"after":after}
    rice_need=sum(i["amount"] for d in menu["dishes"] for i in d["ingredients"] if i["ingredient_id"]=="rice")
    stock=next((i for i in items if i["ingredient_id"]=="rice"),{})
    cook_rice=rice_need>0 and not (stock.get("unit")=="g" and (stock.get("amount") or 0)>=rice_need)
    if cook_rice:
        add("plain_rice","prep_rice","准备菜谱所需熟饭")
    for dish in menu["dishes"]:
        key=dish["id"]
        needs_rice=cook_rice and any(i["ingredient_id"]=="rice" for i in dish["ingredients"])
        add(key,key,dish["name"],["prep_rice/s5"] if needs_rice else [])

    # Keep successive operations in the same vessel together (including passive simmering).
    groups=[]
    owner={}
    for node,t in tasks.items():
        group=len(groups)
        if groups and t["equipment"]:
            prev=groups[-1][-1]
            if prev in t["after"] and tasks[prev]["equipment"]==t["equipment"]:
                group-=1
        if group==len(groups):
            groups.append([])
        groups[group].append(node)
        owner[node]=group
    deps={g:{owner[p] for n in ns for p in tasks[n]["after"] if owner[p]!=g} for g,ns in enumerate(groups)}
    order=list(TopologicalSorter(deps).static_order())
    lengths={g:sum(tasks[n]["minutes"] for n in ns) for g,ns in enumerate(groups)}
    critical={}
    for g in reversed(order):
        critical[g]=lengths[g]+max((critical[c] for c in critical if g in deps[c]),default=0)
    occupied=defaultdict(Counter)
    ends={}
    reservations=[]
    while len(ends)<len(groups):
        ready=[g for g in order if g not in ends and deps[g]<=ends.keys()]
        g=max(ready,key=lambda g:(critical[g],-g))
        start=max((ends[p] for p in deps[g]),default=0)
        slots=[]
        for n in groups[g]:
            t=tasks[n]
            resources=t["equipment"]+(["cooks"] if t["hands"] else [])
            slots.extend([resources]*t["minutes"])
        while any(occupied[start+i][r]>=capacity[r] for i,resources in enumerate(slots) for r in resources):
            start+=1
        for i,resources in enumerate(slots):
            occupied[start+i].update(resources)
        now=start
        for n in groups[g]:
            t=tasks[n]
            reservations.append({**t,"start":now,"end":now+t["minutes"]})
            now+=t["minutes"]
        ends[g]=now
    return dict(id=str(uuid4()),version=menu["version"],minutes=max(ends.values()),
                steps=sorted(reservations,key=lambda t:(t["start"],t["id"])),done=[])
