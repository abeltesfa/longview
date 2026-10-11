"""Score a run from stored beliefs and sales data only (no LLM).

The true cause (region + metric) comes from the scenario recorded with the data (scenarios.py).

Points (max 100):
  35  true cause found (active belief on the true-cause region + metric, confidence >= 0.7)
  25  timeliness: full marks if that belief was reinforced to >= 0.7 by the week after the cause
      first appeared in the data (the scenario's cause_first_week), losing an equal share
      for each week later (see timeliness_points)
  20  minus 10 per wrong belief still active at the end
  10  minus 5 per belief on a non-causal metric (units or revenue)
  10  minus 5 per duplicate belief (created while another active belief had the same region + metric)
"""
from agent import found_true_cause
from scenarios import TRUE_CAUSE_MIN_CONFIDENCE, active_scenario
from analysis import get_metric_series
from memory import ACTIVE, get_beliefs

NON_CAUSAL_METRICS = {"units", "revenue"}


def _retired_week(belief):
    for h in belief["history"]:
        if h["new_status"] != ACTIVE:
            return h["week"]
    return None


def _first_confident_week(beliefs, region, metric):
    weeks = [h["week"] for b in beliefs
             if b["region"] == region and b["metric"] == metric
             for h in b["history"]
             if h["action"] == "reinforced" and h["new_confidence"] >= TRUE_CAUSE_MIN_CONFIDENCE]
    return min(weeks) if weeks else None


def _cause_first_visible_week(scenario, last_week):
    week = scenario.get("cause_first_week")
    return week if week is not None and week <= last_week else None


def _duplicates(beliefs):
    count = 0
    for b in beliefs:
        for o in beliefs:
            if o["_id"] >= b["_id"] or (o["region"], o["metric"]) != (b["region"], b["metric"]):
                continue
            retired = _retired_week(o)
            if retired is None or retired > b["created_week"]:
                count += 1
                break
    return count


def timeliness_points(confident_week, visible_week, last_week):
    """25 points for becoming confident on time, scaled down linearly for each week late.

    "On time" is the week AFTER the cause first appears in the data (same-week still earns full marks).
    A belief only reaches 0.7 through reinforcement, at most once per week. A brand-new hypothesis
    is tested in the week it is created, so same-week confidence is possible when the agent has no
    belief yet. But when the agent reaches the cause by SUPERSEDING an earlier belief (the usual
    path once it already holds a wrong one, as in scenario A), the replacement is first tested the
    following week. Targeting the following week avoids penalizing an agent for changing its mind.
    """
    if confident_week is None or visible_week is None:
        return 0.0
    on_time = visible_week + 1
    span = last_week - on_time + 1
    if span <= 0:
        return 25.0 if confident_week <= last_week else 0.0
    return round(25 * max(0.0, 1 - max(0, confident_week - on_time) / span), 1)


def score_run(last_week):
    scenario = active_scenario()
    region, metric = scenario["true_cause_region"], scenario["true_cause_metric"]
    beliefs = get_beliefs()
    found = found_true_cause()
    first_week = _first_confident_week(beliefs, region, metric)
    visible_week = _cause_first_visible_week(scenario, last_week)
    wrong_active = sum(1 for b in beliefs if b["status"] == ACTIVE and not (
        b["region"] == region and b["metric"] == metric))
    non_causal = sum(1 for b in beliefs if b["metric"] in NON_CAUSAL_METRICS)
    duplicates = _duplicates(beliefs)

    timeliness = timeliness_points(first_week, visible_week, last_week)

    points = {
        "true_cause_found": 35 if found else 0,
        "timeliness": timeliness,
        "no_wrong_active": max(0, 20 - 10 * wrong_active),
        "no_non_causal": max(0, 10 - 5 * non_causal),
        "no_duplicates": max(0, 10 - 5 * duplicates),
    }
    return {
        "scenario": scenario["name"],
        "score": round(sum(points.values()), 1),
        "points": points,
        "found_true_cause": found,
        "true_cause_confident_week": first_week,
        "cause_first_visible_week": visible_week,
        "wrong_active_beliefs": wrong_active,
        "non_causal_beliefs": non_causal,
        "duplicate_beliefs": duplicates,
        "total_beliefs": len(beliefs),
    }
