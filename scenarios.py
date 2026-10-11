"""Planted data scenarios. Each defines its true cause and the week it first appears in the data,
which scoring reads from here. (The week is configured rather than detected because some metrics,
like avg_price, carry noise every week.)

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
        "cause_first_week": 3,
    },
    "B": {
        "name": "B",
        "description": "South: long-running promo active weeks 1-2 ends; promo-driven demand unwinds from "
                       "week 3 (true cause). One-week distribution dip (~-5%) in week 4 is a red herring. "
                       "Price flat.",
        "true_cause_region": "South",
        "true_cause_metric": "promo_active",
        "cause_first_week": 3,
    },
    "C": {
        "name": "C",
        "description": "East: distribution dips ~12% in weeks 2-3 and recovers in week 4 (misleading first "
                       "guess). avg_price rises ~9% from week 3 and stays up; customers drift away over the "
                       "following weeks (true cause). No promo.",
        "true_cause_region": "East",
        "true_cause_metric": "avg_price",
        "cause_first_week": 3,
    },
    "D": {
        "name": "D",
        "description": "North: a one-week promo (week 1) ends in week 2, so revenue dips (misleading first "
                       "guess). Distribution falls ~10% in week 3 and again in week 4, then stays down (true "
                       "cause). Price flat.",
        "true_cause_region": "North",
        "true_cause_metric": "distribution_points",
        "cause_first_week": 3,
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
