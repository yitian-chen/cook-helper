"""Portion and rank whole menus against one shared inventory."""
import math
from collections import defaultdict
from typing import Literal

from pydantic import Field
from .domain import INGREDIENTS, PANTRY_DEFAULTS, RECIPES, Recipe, Strict, allowed
from .meal_lists import analyze_nutrition

RICE = Recipe(id="plain_rice", name="白米饭", servings=2, time_minutes=45,
              cuisine="chinese_home", course="staple", ingredients=[
                  {"ingredient_id":"raw_rice", "amount":140, "unit":"g"}],
              steps=["淘米加水。","煮熟后焖饭。"])
CATALOG = {r.id:r for r in [*RECIPES, RICE]}

class MenuOptions(Strict):
    staple: bool = True
    soup: bool = False
    dishes: int | None = Field(default=None, ge=1, le=10)
    people: int | None = Field(default=None, ge=1, le=20)

def requirements(dishes):
    totals = defaultdict(float)
    for dish in dishes:
        for item in dish["ingredients"]:
            totals[item["ingredient_id"], item["unit"]] += item["amount"]
    return totals

def inventory_details(dishes, items):
    stock = {item["ingredient_id"]:item for item in items}
    details = []
    for (key, unit), amount in requirements(dishes).items():
        have = stock.get(key)
        assumed = key in PANTRY_DEFAULTS and have is None
        missing = round(amount, 2)
        if assumed:
            missing = 0
        elif have and have["amount"] != 0:
            missing = (round(max(0, amount-have["amount"]), 2)
                       if have["unit"] == unit and have["amount"] is not None else None)
        details.append(dict(ingredient_id=key,name=INGREDIENTS[key]["name"],unit=unit,
                            required=round(amount,2),missing=missing,assumed_pantry=assumed))
    return details

def portions(recipe, people, slots):
    share = 1.0
    if slots > 1 and recipe.course != "staple":
        share = min(1, 1.45/max(2,slots-1))
        if recipe.course == "side":
            share *= .8
    factor = people * share / recipe.servings
    rows = []
    for item in recipe.ingredients:
        if item.optional:
            continue
        amount = item.amount * factor
        rows.append(dict(ingredient_id=item.ingredient_id,name=INGREDIENTS[item.ingredient_id]["name"],
                         amount=math.ceil(amount) if item.unit=="piece" else round(amount,1),unit=item.unit))
    return dict(id=recipe.id,name=recipe.name,course=recipe.course,ingredients=rows,
                portions=round(people*share,1))

def rank(recipe, existing, items, people, slots):
    dish = portions(recipe,people,slots)
    combined = inventory_details([*existing,dish],items)
    before = inventory_details(existing,items)
    keys = {i.ingredient_id for i in recipe.ingredients if i.role=="main"}
    stock = {i["ingredient_id"] for i in items if i["amount"] != 0}
    repeated = sum(i["ingredient_id"] in keys for d in existing for i in d["ingredients"])
    new_missing = sum(bool(d["missing"]) for d in combined)-sum(bool(d["missing"]) for d in before)
    return (5*len(keys & stock)/max(1,len(keys))-new_missing-.6*repeated
            -sum(min(1,d["missing"]/d["required"]) for d in combined if d["missing"])
            -.003*recipe.time_minutes)

def make_menu(items, prefs, options, revision, previous=None, replace=None, wanted=None, preferred=()):
    people = options.people or prefs.servings
    slots = options.dishes if options.dishes is not None else min(8, 2+(people-1)//3)+int(options.soup)
    if people==1 and options.dishes is None and options.staple and not options.soup:
        roles = ["mixed_staple"]
    else:
        roles = ["main" if i%2==0 else "side" for i in range(slots)]
        if options.soup:
            roles[-1]="soup"
        if options.staple:
            roles.append("rice")
    unused_preferred = set(preferred) & {i["ingredient_id"] for i in items if i["amount"] != 0}
    chosen = []
    if replace:
        if not previous or replace not in [d["id"] for d in previous["dishes"]]:
            raise ValueError("请选择当前餐单中的菜")
        chosen = [d for d in previous["dishes"] if d["id"]!=replace]
        roles = [CATALOG[replace].course]
    for role in roles:
        candidates = [r for r in CATALOG.values() if allowed(r,prefs)
                      and r.id not in {replace, *(d["id"] for d in chosen)}]
        if role=="rice":
            candidates = [r for r in candidates if r.id==RICE.id and "rice" not in prefs.excluded_ingredient_ids]
            if not candidates:
                candidates=[r for r in RECIPES if r.course=="staple" and allowed(r,prefs)]
        elif role=="mixed_staple":
            candidates=[r for r in candidates if r.course=="staple" and len(r.ingredients)>3]
        else:
            candidates=[r for r in candidates if r.course==role]
        if wanted:
            candidates=[r for r in candidates if any(i.ingredient_id==wanted for i in r.ingredients)]
        if not candidates:
            raise ValueError("当前饮食限制或指定食材下没有合适的"+role+"，请调整餐单要求")
        count = len(previous["dishes"]) if replace else len(roles)
        def priority(recipe):
            featured = {i.ingredient_id for i in recipe.ingredients if i.role != "seasoning"}
            return len(featured & unused_preferred), rank(recipe,chosen,items,people,count)
        best=max(candidates,key=priority)
        unused_preferred.difference_update(i.ingredient_id for i in best.ingredients if i.role != "seasoning")
        chosen.append(portions(best,people,count))
    if replace:
        replacement=chosen[-1]
        chosen=[replacement if d["id"]==replace else d for d in previous["dishes"]]
    return dict(version=revision,people=people,options=options.model_dump(),dishes=chosen)

def meal_reports(menu, items):
    details=inventory_details(menu["dishes"],items)
    return ({"buy":[d for d in details if d["missing"]],
             "available_unmeasured":[d for d in details if d["missing"] is None]},
            analyze_nutrition({"details":details,"servings":menu["people"]}))
