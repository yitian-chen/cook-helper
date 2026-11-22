import yaml
from .domain import ROOT

NUTRIENTS = yaml.safe_load((ROOT / "data/nutrition.yaml").read_text())
METRICS = ("kcal", "protein", "fat", "carbs")

def generate_shopping_list(recipe):
    return [detail for detail in recipe["details"] if detail["status"] != "enough"]

def analyze_nutrition(recipe):
    totals = dict.fromkeys(METRICS, 0.0)
    unavailable = []
    for detail in recipe["details"]:
        profile = NUTRIENTS.get(detail["ingredient_id"], {})
        scale = 1 if detail["unit"] == "g" else profile.get("grams_per_piece") if detail["unit"] == "piece" else None
        if not profile or scale is None:
            unavailable.append(detail["name"])
            continue
        for key in METRICS:
            totals[key] += profile[key] * detail["required"] * scale / 100
    return {"total":{k:round(v,1) for k,v in totals.items()},
            "per_person":{k:round(v / recipe["servings"],1) for k,v in totals.items()},
            "unavailable":unavailable, "servings":recipe["servings"]}
