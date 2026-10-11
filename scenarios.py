"""Planted data scenarios. Each defines its true cause, which scoring reads from here.

generate_data.py records the scenario it loaded in the database's `scenario` collection,
so scoring always matches the data actually in that database.
"""
from db import get_db

SCENARIOS = {
    "A": {
        "name": "A",
        "description": "West: price hike weeks 2-3 (red herring), distribution loss ~15%/week from week 3 "
                       "(true cause), promo in week 4 doesn't help.",
        "true_cause_region": "West",
        "true_cause_metric": "distribution_points",
    },
    "B": {
        "name": "B",
        "description": "South: long-running promo active weeks 1-2 ends; promo-driven demand unwinds from "
                       "week 3 (true cause). One-week distribution dip (~-5%) in week 4 is a red herring. "
                       "Price flat.",
        "true_cause_region": "South",
        "true_cause_metric": "promo_active",
    },
}
DEFAULT_SCENARIO = "A"
TRUE_CAUSE_MIN_CONFIDENCE = 0.7


def active_scenario():
    """The scenario recorded in the current database (scenario A if none was recorded)."""
    doc = get_db()["scenario"].find_one({"_id": "current"})
    return SCENARIOS[doc["name"] if doc else DEFAULT_SCENARIO]


def record_scenario(name):
    get_db()["scenario"].replace_one({"_id": "current"}, {"_id": "current", "name": name}, upsert=True)
