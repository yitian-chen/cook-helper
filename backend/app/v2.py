import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from . import store, v2_state
from .cooking import Kitchen, schedule
from .domain import Preferences, Strict, PANTRY_PROMPT, INGREDIENTS
from .llm_client import LLMClient, LLMError
from .meal import interpret, recognize_ingredients, respond_to_request
from .replacements import Swap, alternatives, apply_swap, undo_swap
from .menu import CATALOG, MenuOptions, make_menu, meal_reports

router=APIRouter(prefix="/api/v2")

class Versioned(Strict):
    session_id:str = v2_state.DEFAULT
    revision:int = Field(ge=0)
    preferences_version:int

class Turn(Versioned):
    text:str=Field(default="",max_length=12000)
    image_id:str|None=None
    action:Literal["chat","replace","choose","undo","confirm","ready","schedule","replan"]="chat"
    dish_id:str|None=None
    target_id:str|None=None

class Settings(Versioned):
    kitchen:Kitchen

class Progress(Versioned):
    timeline_id:str
    step_id:str
    done:bool

class Preview(Versioned):
    dish_id:str

@router.post("/alternatives")
def preview(body:Preview):
    state=current(body)
    if not state["menu"] or body.dish_id not in {d["id"] for d in state["menu"]["dishes"]}:
        raise HTTPException(409,"请选择当前餐单中的菜")
    prefs=Preferences.model_validate({k:v for k,v in store.preferences().items() if k!="version"})
    return alternatives(state,prefs,Swap(dish_id=body.dish_id),store.get("v2:kitchen"))

def current(body):
    state=v2_state.active(body.session_id)
    if (state["revision"],state["preferences_version"])!=(body.revision,body.preferences_version):
        raise HTTPException(409,"餐单已变化，请刷新")
    return state

@router.get("/sessions")
def sessions():
    return v2_state.list_sessions()

@router.post("/sessions",status_code=201)
def create_session():
    return v2_state.new_session()

@router.get("/current")
def get_current(session_id:str=v2_state.DEFAULT):
    return {**v2_state.active(session_id),"kitchen":store.get("v2:kitchen")}

@router.put("/kitchen")
def configure(body:Settings):
    state=current(body)
    state["timeline"]=None
    return v2_state.save(state,body.revision,body.kitchen.model_dump())

@router.put("/progress")
def progress(body:Progress):
    state=v2_state.active(body.session_id)
    timeline=state["timeline"]
    if state["preferences_version"]!=body.preferences_version or not timeline or timeline["id"]!=body.timeline_id:
        raise HTTPException(409,"时间线已更新")
    steps={s["id"]:s for s in timeline["steps"]}
    if body.step_id not in steps:
        raise HTTPException(404,"步骤不存在")
    done=set(timeline["done"])
    if (body.step_id in done)==body.done:
        return state
    if body.revision!=state["revision"]:
        raise HTTPException(409,"进度已更新，请刷新")
    if body.done:
        if set(steps[body.step_id]["after"])-done:
            raise HTTPException(409,"请先完成前置步骤")
        done.add(body.step_id)
    else:
        if any(body.step_id in steps[s]["after"] for s in done):
            raise HTTPException(409,"请先撤销后续步骤")
        done.remove(body.step_id)
    timeline["done"]=sorted(done)
    return v2_state.save(state,body.revision)

HELP={
 "recognize_ingredients":"识别上传的图片",
 "write_inventory":"保存这条消息中的食材及身份澄清。工具直接读取照片和原始消息，请传空对象 {}。",
 "compose_menu":"按人数和用户明确要求生成整餐。dishes 为菜数，不含白饭；soup 包含于菜数。",
 "replace_dish":"比较替代菜，指定原菜 ID。可选主要食材、排除食材、避用设备或快手。仅用户点名目标菜时传 target_name 中文菜名",
 "choose_dish":"应用用户选中的替代菜",
 "undo_swap":"撤销上次换菜",
 "generate_shopping_list":"计算整餐采买和余量未知项",
 "analyze_nutrition":"计算整餐及每人营养",
 "confirm_menu":"仅在用户明确选定当前餐单并决定做这些菜时调用。",
 "confirm_ready":"仅在用户明确表示当前餐单食材已经备齐时调用",
 "generate_timeline":"按已确认餐单和厨房配置生成时间线",
}

def menu_confirmed(state):
    return state.get("confirmed", state["ready"])


def menu_summary(menu):
    if not menu:
        return None
    return {"people":menu["people"],"dishes":[
        {k:dish[k] for k in ("id","name","course")} for dish in menu["dishes"]]}


def schema(name,state):
    params={"type":"object","properties":{},"additionalProperties":False}
    if name=="compose_menu":
        params=MenuOptions.model_json_schema()
    if name=="replace_dish":
        params=Swap.model_json_schema()
        params["properties"]["ingredient_id"]["description"]="使用食材库 ID，例如 beef、tofu、egg"
        params["properties"]["dish_id"]["enum"]=[d["id"] for d in state["menu"]["dishes"]]
    return {"type":"function","function":{"name":name,"description":HELP[name],"parameters":params}}

async def assistant_turn(state,text,*,body,report=None):
    prefs=Preferences.model_validate({k:v for k,v in store.preferences().items() if k!="version"})
    kitchen=store.get("v2:kitchen")
    old=state["menu"]
    if body.action not in ("chat","replan") and not old:
        raise HTTPException(409,"请先生成餐单")
    if body.action=="schedule" and (not state["ready"] or not kitchen):
        raise HTTPException(409,"请先确认食材备齐并配置厨房")
    if body.action in ("replace","choose") and body.dish_id not in [d["id"] for d in old["dishes"]]:
        raise HTTPException(409,"请选择当前餐单中的菜")
    trigger={"confirm":"就做这套餐单，请告诉我需要买什么，以及这一餐的营养。","replace":"换这道菜 "+(CATALOG[body.dish_id].name if body.dish_id in CATALOG else ""),"ready":"这套餐单的食材已经备齐了。",
             "schedule":"使用已保存的厨房配置生成时间线。","replan":"按最新用餐人数和饮食偏好重新配餐。"}
    if body.action=="choose":
        if body.target_id not in CATALOG:
            raise HTTPException(422,"请选择有效的替代菜")
        trigger["choose"]=f"把{CATALOG[body.dish_id].name}换成{CATALOG[body.target_id].name}"
    if body.action=="undo" and not state.get("undo_swap"):
        raise HTTPException(409,"当前没有可撤销的换菜")
    trigger["undo"]="撤销刚才的换菜"
    text=text or trigger.get(body.action,"请根据照片安排一餐")
    policy=("你是灶边整餐助手。用户报食材或澄清时调用write_inventory，图片先识别。仅身份或过敏有歧义时追问，"
            "用户说一块、一把、一些，表示该食材可直接入菜；无需称重、换算或确认是否够。依照当前请求给compose_menu参数：people人数、staple主食、soup汤、dishes菜数。"
            "用户补充食材先写库存，再优先安排使用它的合规菜。没有入选的食材不要归因于重量不明。补充库存后保留上一餐偏好。要求替换时用replace_dish，想换成豆腐时ingredient_id用tofu。"
            "比较换菜只调用replace_dish，按本轮要求填写ingredient_id、exclude_ingredients、avoid_equipment、quick，仅用户明确指定目标菜名或选定已有候选序号才填target_name。没有明确原菜时先询问。候选只供选择，勿称已换菜。首次配餐只介绍菜名、搭配和人数，并邀请用户确定餐单。换一道菜不等于确定整餐。确认整餐后才调用清单及营养工具，并用简洁 Markdown 汇报采购和每人营养，标明估算及未覆盖项。不要逐项报食材克数或列出库存量。"
            "仅在用户明确表示食材已备齐时调用confirm_ready。厨房未设置就提示配置。时间线生成后简述总时长和并行安排。"
            "整套菜单以最新工具结果 dishes 数组为准，换下的菜不再呈现。食材份数是分享份量。营养未覆盖项不按零处理。不要仅在口头声称已完成工具操作。遵守过敏忌口限制，不推荐菜单外的配料。"+PANTRY_PROMPT)
    context={"偏好":prefs.model_dump(),"当前库存":state["items"],"未决问题":state["questions"],
             "候选菜":[{"id":x["id"],"name":x["name"]} for x in (state.get("swap_preview") or {}).get("options",[])],"当前菜单":menu_summary(old),"已确定餐单":menu_confirmed(state),"食材备齐":state["ready"],"厨房":kitchen,"有图片":bool(body.image_id)}
    messages=[{"role":"system","content":policy},{"role":"system","content":json.dumps(context,ensure_ascii=False)}]
    messages.extend({k:m[k] for k in ("role","content")} for m in state["messages"][-12:])
    messages.append({"role":"user","content":text})
    client=LLMClient()
    outputs={}
    pending=[]
    if body.image_id:
        pending=["recognize_ingredients","write_inventory"]
    elif body.action!="chat":
        pending=[{"confirm":"confirm_menu","ready":"confirm_ready","schedule":"generate_timeline","replace":"replace_dish","choose":"choose_dish","undo":"undo_swap","replan":"compose_menu"}[body.action]]
    for iteration in range(12):
        names=pending[:1] if pending else []
        if not outputs and not pending:
            names=["write_inventory","compose_menu"]
            if old:
                names.extend(["replace_dish","confirm_menu","confirm_ready"])
                if state.get("undo_swap"):
                    names.append("undo_swap")
        prompt=messages
        if not names and not menu_confirmed(state):
            prompt=[*messages,{"role":"system","content":"当前餐单还未确定。此轮只介绍菜名、搭配和人数，或回答选菜。请勿列出缺少的食材、采购建议或营养，即使作为附注也留到用户选定餐单后。"}]
        elif not names and any(tool in outputs for tool in ("choose_dish","replace_dish","undo_swap")):
            prompt=[*messages,{"role":"system","content":"这是换菜交互。候选在原菜卡片下。请简述选择或实际变动。只有餐单已确定时才提采购和营养。"}]
        reply=await client.chat(prompt,[schema(n,state) for n in names] or None)
        calls=reply.get("tool_calls") or []
        if not calls:
            content=reply.get("content")
            if pending or not isinstance(content,str) or not content.strip():
                messages.append({"role":"user","content":"请先完成必需的工具操作，再回复。"})
                continue
            if client.mode!="mock" and (prefs.allergens or prefs.dietary_rules or prefs.excluded_ingredient_ids):
                review=await client.chat([{"role":"system","content":"审核饮食建议是否符合限制，仅回复 PASS 或 BLOCK："+prefs.model_dump_json()},
                                          {"role":"user","content":content}])
                if (review.get("content") or "").strip()!="PASS":
                    messages.extend([reply,{"role":"user","content":"按限制修正建议。"}])
                    continue
            state["messages"].extend([{"role":"user","content":text,"image":body.image_id},{"role":"assistant","content":content}])
            return v2_state.save(state,body.revision)
        messages.append(reply)
        for index,call in enumerate(calls):
            fn=call["function"]
            name=fn["name"]
            event_id=f"{iteration}-{index}"
            try:
                if name not in names or (pending and pending[0]!=name) or (not pending and outputs):
                    raise ValueError("当前阶段无法使用此工具")
                # Bound tools read the request and session, never model-supplied payloads.
                args=json.loads(fn["arguments"]) if name in ("compose_menu","replace_dish") else {}
                if report:
                    report("tool",id=event_id,name=name,status="running")
                next_tools=[]
                if name=="recognize_ingredients":
                    output=await recognize_ingredients(body.image_id)
                elif name=="write_inventory":
                    vision=outputs.get("recognize_ingredients",{})
                    previous={i["ingredient_id"]:i for i in state["items"]}
                    items={**previous, **{i["ingredient_id"]:i for i in vision.get("items",[])}}
                    questions=list(dict.fromkeys(state["questions"]+vision.get("uncertain",[])))
                    if body.text:
                        change=await interpret(body.text,list(items.values()),state["extra_foods"],questions)
                        if change.intent=="inventory":
                            items={i.ingredient_id:i.model_dump() for i in change.items}
                            questions=change.questions
                            state["extra_foods"]=change.extra_foods
                        elif not vision:
                            outputs[name]={"库存未变更":True}
                            messages.append({"role":"tool","tool_call_id":call["id"],
                                             "content":json.dumps(outputs[name],ensure_ascii=False)})
                            if report:
                                report("tool",id=event_id,name=name,status="completed")
                            continue
                    state.update(items=list(items.values()),questions=questions)
                    state["preferred_foods"]=[
                        key for key,item in items.items() if item["amount"]!=0
                        and (previous.get(key)!=item or INGREDIENTS[key]["name"] in body.text)
                    ]
                    if old:
                        state["prior_options"]=old["options"]
                    if not state["items"] and not state["extra_foods"] and not state["questions"]:
                        state["questions"]=["手边有哪些可以用的食材？"]
                    state.update(swap_preview=None,undo_swap=None,menu=None,confirmed=False,ready=False,timeline=None,shopping=None,nutrition=None)
                    output={"库存":state["items"],"其他":state["extra_foods"],"需澄清身份":state["questions"]}
                    if not state["questions"]:
                        next_tools=["compose_menu"]
                elif name in ("choose_dish","replace_dish"):
                    if body.action in ("replace","choose"):
                        args["dish_id"]=body.dish_id
                    if name=="choose_dish":
                        args["target_name"]=CATALOG[body.target_id].name
                    swap=Swap.model_validate(args)
                    preview=alternatives(state,prefs,swap,kitchen)
                    previous=state.get("swap_preview") or {}
                    ordinal_choice=any(
                        previous.get("dish_id")==swap.dish_id and option["name"]==swap.target_name
                        and any(token in body.text for token in (f"第{index}","第"+"一二三"[index-1]))
                        for index,option in enumerate(previous.get("options",[]),1)
                    )
                    explicit=(body.action=="chat" and not state["timeline"] and swap.target_name
                              and (swap.target_name in body.text or ordinal_choice))
                    if preview["options"] and (name=="choose_dish" or explicit):
                        target=preview["options"][0]
                        apply_swap(state,swap.dish_id,target["id"],kitchen)
                        output={"已换成":target["name"],"原菜":CATALOG[swap.dish_id].name}
                        if menu_confirmed(state):
                            next_tools=["generate_shopping_list","analyze_nutrition"]
                    else:
                        state["swap_preview"]=preview
                        output={"尚未换菜":True,"候选":[option["name"] for option in preview["options"]],"已有时间线":bool(state["timeline"])}
                elif name=="undo_swap":
                    undo_swap(state,kitchen)
                    output={"已恢复餐单":menu_summary(state["menu"])}
                    if menu_confirmed(state):
                        next_tools=["generate_shopping_list","analyze_nutrition"]
                elif name=="compose_menu":
                    if state["questions"]:
                        raise ValueError("先澄清食材身份")
                    options=MenuOptions.model_validate((old or {}).get("options",state.get("prior_options",{})))
                    if body.action=="replan":
                        args["people"]=prefs.servings
                    options=MenuOptions.model_validate({**options.model_dump(),**args})
                    output=make_menu(state["items"],prefs,options,(old or {}).get("version",0)+1,
                                     preferred=state.get("preferred_foods",[]))
                    state.update(swap_preview=None,undo_swap=None,menu=output,confirmed=False,ready=False,
                                 timeline=None,shopping=None,nutrition=None)
                    output=menu_summary(output)
                elif name=="confirm_menu":
                    state["confirmed"]=True
                    output={"已确定餐单":menu_summary(state["menu"])}
                    next_tools=["generate_shopping_list","analyze_nutrition"]
                elif name in ("generate_shopping_list","analyze_nutrition"):
                    shopping,nutrition=meal_reports(state["menu"],state["items"])
                    key,output=("shopping",shopping) if name=="generate_shopping_list" else ("nutrition",nutrition)
                    state[key]=output
                    if name=="generate_shopping_list":
                        output={"待买食材":[row["name"] for row in shopping["buy"]]}
                elif name=="confirm_ready":
                    state["undo_swap"]=None
                    if not menu_confirmed(state):
                        next_tools=["generate_shopping_list","analyze_nutrition"]
                    state["confirmed"]=True
                    state["ready"]=True
                    output={"食材备齐":True,"需要配置厨房":not kitchen}
                    if kitchen and not state["timeline"]:
                        next_tools.append("generate_timeline")
                else:
                    if not state["ready"] or not kitchen:
                        raise ValueError("请先确认食材备齐并配置厨房")
                    try:
                        timeline=state["timeline"] or schedule(state["menu"],Kitchen.model_validate(kitchen),state["items"])
                    except ValueError as exc:
                        output={"厨房冲突":str(exc)}
                    else:
                        state["timeline"]=timeline
                        output={"总分钟":timeline["minutes"],"步骤":[[t["start"],t["end"],t["dish"],t["action"]] for t in timeline["steps"]]}
                outputs[name]=output
                if pending and pending[0]==name:
                    pending.pop(0)
                pending.extend(next_tools)
                if report:
                    report("tool",id=event_id,name=name,status="completed")
            except (TypeError,ValueError) as exc:
                output={"error":str(exc)}
                if report:
                    report("tool",id=event_id,name=name,status="failed")
                # An impossible menu should be explained rather than retried forever.
                if isinstance(exc,ValueError) and str(exc).startswith("当前饮食限制"):
                    pending.clear()
                    outputs[name]=output
            messages.append({"role":"tool","tool_call_id":call["id"],"content":json.dumps(output,ensure_ascii=False)})
    raise LLMError("助手未完成本轮操作，请重试")

@router.post("/turn")
async def turn(body:Turn,request:Request):
    state=current(body)
    if not body.text.strip() and not body.image_id and body.action=="chat":
        raise HTTPException(422,"请发送食材或问题")
    return await respond_to_request(request,state,body.text,runner=assistant_turn,body=body)
