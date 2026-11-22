"""Conversation inventory and meal-selection tools."""
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import Field

from . import store
from .agent import execute
from .domain import INGREDIENTS, Item, Strict, validate_items
from .llm_client import LLMClient, LLMError
from .vision import recognize_ingredients
from .meal_lists import analyze_nutrition, generate_shopping_list

router = APIRouter()

class MealTurn(Strict):
    meal_id: str | None = None
    image_id: str | None = None
    text: str = Field(default="", max_length=12000)
    preferences_version: int

class InventoryChange(Strict):
    intent: Literal["inventory", "chat"]
    items: list[Item] = Field(default_factory=list, max_length=100)
    extra_foods: list[str] = Field(default_factory=list, max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=20)

async def interpret(text, items, extras, questions):
    client = LLMClient()
    if client.mode == "mock":
        raise LLMError("离线模式支持示例图片；自然语言食材识别需要连接模型")
    context = {"库存": items, "其他食材": extras, "未决问题": questions, "用户消息": text,
               "字典": {key: value["name"] for key, value in INGREDIENTS.items()}}
    instruction = (
        "解释厨房助手的用户消息，仅返回 JSON 对象。普通聊天、询问做法或营养不是库存声明，返回"
        '{"intent":"chat"}。声明、补充、更正、回答食材问题或要求根据库存推荐时返回'
        '{"intent":"inventory","items":[{"ingredient_id":"标准ID","amount":数值或null,"unit":"piece或g或ml"}],'
        '"extra_foods":["字典外已知食材及数量"],"questions":["剩余需要询问的食材身份问题"]}。'
        "items 和 extra_foods 返回修改后的完整库存。保留未提及食材；还有/再加表示新增数量，实际共有表示更正。"
        "用户排除或跳过的物品移除。只记录实际拥有的东西，未给数量用null，不能猜重量，"
        "数量未知无需询问。只移除已回答或明确跳过的问题，其他疑问保留。"
    )
    reply = await client.chat([{"role":"system","content":instruction},
                               {"role":"user","content":json.dumps(context,ensure_ascii=False)}])
    try:
        raw = reply["content"].strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1])
        change = InventoryChange.model_validate_json(raw)
        validate_items(change.items)
        return change
    except (ValueError, KeyError, AttributeError):
        raise LLMError("食材消息解析失败，请重新发送") from None

def write_inventory(run, items, extras, questions):
    run.update(items=items, extra_foods=extras, uncertain=questions, result=None)
    run["events"].append({"tool":"write_inventory","ok":True})
    store.put("run:" + run["id"], run)

def load_meal(meal_id, version):
    prefs = store.preferences()
    if prefs["version"] != version:
        raise HTTPException(409, "饮食偏好已更新，请刷新")
    if not meal_id:
        return {"items":[], "extra_foods":[], "uncertain":[], "preferences_version":version,
                "preferences":{k:v for k,v in prefs.items() if k!="version"}}
    meal = store.get("run:" + meal_id)
    if not meal or meal["preferences_version"] != version:
        raise HTTPException(409, "该轮推荐已失效，请重新发送食材")
    return meal

@router.post("/api/meal-turn")
async def meal_turn(body: MealTurn):
    previous = load_meal(body.meal_id, body.preferences_version)
    items = previous["items"]
    extras = previous.get("extra_foods", [])
    questions = previous.get("uncertain", [])
    run = {**previous, "id":str(uuid4()), "events":[], "status":"running"}
    run.pop("caption_question", None)
    try:
        if body.image_id:
            vision = await recognize_ingredients(body.image_id)
            by_id = {item["ingredient_id"]:item for item in items}
            by_id.update({item["ingredient_id"]:item for item in vision["items"]})
            items = list(by_id.values())
            questions = list(dict.fromkeys([*questions, *vision["uncertain"]]))
            run.update(vision_provider=vision["provider"])
            run["events"].append({"tool":"recognize_ingredients","ok":True})
        if body.text.strip():
            change = await interpret(body.text, items, extras, questions)
            if change.intent == "inventory":
                items = [item.model_dump() for item in change.items]
                extras, questions = change.extra_foods, change.questions
            elif not body.image_id:
                return {"chat":True}
            else:
                run["caption_question"] = body.text
        if not items and not extras and not questions:
            questions = ["有哪些可以用的食材？可以直接告诉我名称。"]
        load_meal(None, body.preferences_version)
        write_inventory(run, items, extras, questions)
        if questions:
            run["status"] = "clarifying"
            store.put("run:" + run["id"], run)
            return run
        await execute(run["id"], "match")
        finished = store.get("run:" + run["id"])
        if finished["status"] != "completed":
            raise LLMError(finished.get("error") or "菜谱匹配失败")
        return finished
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from None

class Selection(Strict):
    recipe_id: str
    preferences_version: int

@router.post("/api/meals/{meal_id}/select")
def select(meal_id: str, body: Selection):
    meal = load_meal(meal_id, body.preferences_version)
    recipe = next((r for r in (meal.get("result") or {}).get("recipes", [])
                   if r["id"] == body.recipe_id), None)
    if not recipe:
        raise HTTPException(422, "请选择本轮推荐中的菜谱")
    return {"shopping":generate_shopping_list(recipe), "nutrition":analyze_nutrition(recipe)}
