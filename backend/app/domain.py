from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, Field, ConfigDict, model_validator

ROOT = Path(__file__).resolve().parents[2]
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
class Item(Strict):
    ingredient_id: str
    amount: float | None = Field(default=None, ge=0, le=100000)
    unit: Literal["g", "piece", "ml"] = "g"
class Preferences(Strict):
    allergens: list[Literal["egg", "soy", "wheat", "milk", "peanut", "fish", "shellfish", "sesame"]] = []
    excluded_ingredient_ids: list[str] = []
    dietary_rules: list[Literal["vegetarian"]] = []
    servings: int = Field(default=2, ge=1, le=20)
class Requirement(Item):
    amount: float = Field(gt=0)
    role: Literal["main", "side", "seasoning"] = "main"
    optional: bool = False
class Recipe(Strict):
    schema_version: Literal[1] = 1
    id: str
    name: str
    servings: int = Field(gt=0)
    time_minutes: int = Field(gt=0)
    cuisine: Literal["chinese_home", "sichuan", "jiangzhe", "dongbei", "shandong", "cantonese", "western"]
    course: Literal["main", "side", "soup", "staple", "dessert"]
    tags: list[str] = []
    ingredients: list[Requirement] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    @model_validator(mode="after")
    def required(self):
        if not any(not i.optional for i in self.ingredients):
            raise ValueError("需要必需食材")
        return self

INGREDIENTS = yaml.safe_load((ROOT / "data/ingredients.yaml").read_text())
RECIPES = [Recipe.model_validate(yaml.safe_load(p.read_text())) for p in sorted((ROOT / "data/recipes").glob("*.yaml"))]
assert len({r.id for r in RECIPES}) == len(RECIPES)
assert all(i.ingredient_id in INGREDIENTS for r in RECIPES for i in r.ingredients)

PANTRY_DEFAULTS = frozenset({"cooking_oil", "salt", "soy_sauce", "rice", "raw_rice", "sugar", "vinegar"})
PANTRY_PROMPT = (
    "默认家里备有食用油、盐、酱油、糖、醋和大米。大米可以煮成米饭，但不代表已有熟饭或隔夜饭。"
    "这些是常备假设，不是已盘点的库存。用户明确说明缺少、用量不足或忌口时，以用户提供的信息为准。"
    "工具标记为 assumed_pantry 的食材无需列入待买清单，不要声称已核实其库存数量。"
)

def validate_items(items):
    if len(items) > 100 or len({x.ingredient_id for x in items}) != len(items):
        raise ValueError("食材不能重复，且最多 100 项")
    if any(x.ingredient_id not in INGREDIENTS for x in items):
        raise ValueError("未知食材，请选择标准名称")

def allowed(recipe, prefs):
    for item in recipe.ingredients:
        meta = INGREDIENTS[item.ingredient_id]
        if item.ingredient_id in prefs.excluded_ingredient_ids:
            return False
        if set(meta["allergens"]) & set(prefs.allergens):
            return False
        if prefs.allergens and not meta.get("composition_known", True):
            return False
        if "vegetarian" in prefs.dietary_rules and meta.get("meat"):
            return False
    return True

def match_recipes(items, prefs):
    validate_items(items)
    inventory = {i.ingredient_id: i for i in items}
    matches, excluded = [], []
    for recipe in RECIPES:
        if not allowed(recipe, prefs):
            excluded.append({"id": recipe.id, "reason": "与过敏、忌口或饮食规则冲突"})
            continue
        coverage = adequacy = total = 0
        details = []
        for req in recipe.ingredients:
            if req.optional:
                continue
            weight = {"main": 3, "side": 2, "seasoning": 1}[req.role]
            need = req.amount * prefs.servings / recipe.servings
            have = inventory.get(req.ingredient_id)
            assumed_pantry = have is None and req.ingredient_id in PANTRY_DEFAULTS
            present = assumed_pantry or (have is not None and (have.amount is None or have.amount > 0))
            known = have is not None and have.amount is not None and (have.amount == 0 or have.unit == req.unit)
            available = have.amount if known else 0
            coverage += weight * present
            adequacy += weight * (1 if assumed_pantry else min(available / need, 1))
            total += weight
            details.append({"ingredient_id": req.ingredient_id, "name": INGREDIENTS[req.ingredient_id]["name"],
                            "required": need, "unit": req.unit, "available": available if known else None,
                            "missing": 0 if assumed_pantry else max(need - available, 0) if known or not present else None,
                            "assumed_pantry": assumed_pantry,
                            "status": "enough" if assumed_pantry else "missing" if not present else "unknown" if not known else "enough" if available >= need else "short"})
        matches.append({**recipe.model_dump(), "servings": prefs.servings,
                        "score": round(100 * (.7 * coverage / total + .3 * adequacy / total), 2),
                        "coverage": round(coverage / total, 3), "adequacy": round(adequacy / total, 3),
                        "details": details})
    matches.sort(key=lambda r: (-r["score"], sum(d["status"] == "missing" for d in r["details"]), r["time_minutes"], r["id"]))
    return {"recipes": matches[:3], "excluded": excluded}
