"""Compare a single dish change in the context of the complete meal."""
from pydantic import Field

from .cooking import Resource, STEPS
from .domain import INGREDIENTS, Strict, allowed
from .menu import CATALOG, inventory_details, portions, rank


class Swap(Strict):
    dish_id: str
    ingredient_id: str | None = None
    exclude_ingredients: list[str] = Field(default_factory=list)
    avoid_equipment: list[Resource] = Field(default_factory=list)
    quick: bool = False
    target_name: str | None = None


def shopping_amounts(dishes, items):
    return {(row["name"], row["unit"]): row["missing"]
            for row in inventory_details(dishes, items) if row["missing"]}


def alternatives(state, prefs, swap, kitchen):
    menu = state["menu"]
    existing = [d for d in menu["dishes"] if d["id"] != swap.dish_id]
    if len(existing) == len(menu["dishes"]):
        raise ValueError("请选择当前餐单中的菜")
    original = CATALOG[swap.dish_id]
    items = state["items"]
    stock = {i["ingredient_id"] for i in items if i["amount"] != 0}
    preferred = set(state.get("preferred_foods", []))
    used = {d["id"] for d in menu["dishes"]}
    before = shopping_amounts(menu["dishes"], items)
    candidates = []
    for recipe in CATALOG.values():
        if recipe.id in used or not allowed(recipe, prefs):
            continue
        if swap.target_name and recipe.name != swap.target_name:
            continue
        if not swap.target_name and recipe.course != original.course and {recipe.course, original.course} != {"main","side"}:
            continue
        featured = {i.ingredient_id for i in recipe.ingredients if not i.optional and i.role != "seasoning"}
        if swap.ingredient_id and swap.ingredient_id not in featured:
            continue
        if any(i.ingredient_id in swap.exclude_ingredients for i in recipe.ingredients):
            continue
        equipment = {e for step in STEPS[recipe.id] for e in step["equipment"]}
        if kitchen and "rice_cooker" in equipment and not kitchen["rice_cooker"]:
            equipment = equipment - {"rice_cooker"} | {"pot","burner"}
        if equipment.intersection(swap.avoid_equipment) or kitchen and any(not kitchen[e] for e in equipment):
            continue
        count = len(menu["dishes"])
        dish = portions(recipe, menu["people"], count)
        after = shopping_amounts([*existing, dish], items)
        added = [dict(name=n, unit=u, amount=round(q-before.get((n,u),0),2))
                 for (n,u),q in after.items() if q > before.get((n,u),0)]
        saved = [dict(name=n, unit=u, amount=round(q-after.get((n,u),0),2))
                 for (n,u),q in before.items() if q > after.get((n,u),0)]
        have = sorted(featured & stock, key=lambda k:(k not in preferred,k))
        reasons = []
        if have:
            reasons.append("用上现有的" + "、".join(INGREDIENTS[k]["name"] for k in have[:3]))
        if "burner" not in equipment:
            reasons.append("不用占灶眼")
        elif original.time_minutes > recipe.time_minutes:
            reasons.append(f"比原菜约快 {original.time_minutes-recipe.time_minutes} 分钟")
        if not reasons:
            reasons.append("主要食材是" + "、".join(INGREDIENTS[k]["name"] for k in sorted(featured)[:3]))
        reason = "，".join(reasons)
        row = dict(id=recipe.id,name=recipe.name,reason=reason,minutes=recipe.time_minutes,
                   added=added,saved=saved,needs_shopping=bool(after),equipment=sorted(equipment))
        score = rank(recipe,existing,items,menu["people"],count) + 3*len(preferred & featured & stock)
        candidates.append((row, featured, score))
    result = []
    covered = set()
    while candidates and len(result) < 3:
        def priority(entry):
            row, foods, score = entry
            overlap = len(foods & covered)/max(1,len(foods))
            return (-row["minutes"] if swap.quick else 0, score - 4*overlap, -row["minutes"])
        best = max(candidates,key=priority)
        result.append(best[0])
        covered.update(best[1])
        candidates.remove(best)
    return dict(dish_id=swap.dish_id,options=result)


def apply_swap(state, source, target, kitchen):
    menu = state["menu"]
    replacement = portions(CATALOG[target], menu["people"], len(menu["dishes"]))
    dishes = [replacement if d["id"] == source else d for d in menu["dishes"]]
    state["undo_swap"] = {k:state[k] for k in ("menu","ready","timeline")}
    state["undo_swap"]["kitchen"] = kitchen
    state["confirmed"] = state.get("confirmed", state["ready"])
    state.update(menu={**menu,"version":menu["version"]+1,"dishes":dishes},
                 ready=False,timeline=None,swap_preview=None)


def undo_swap(state,kitchen):
    previous = state.pop("undo_swap")
    version = state["menu"]["version"] + 1
    timeline = previous["timeline"] if kitchen == previous["kitchen"] else None
    state.update(menu={**previous["menu"],"version":version}, ready=previous["ready"],
                 timeline={**timeline,"version":version} if timeline else None,swap_preview=None)
