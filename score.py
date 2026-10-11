"""Score a run from stored beliefs and sales data only (no LLM).

Points (max 100):
  35  true cause found (active West belief on distribution_points, confidence >= 0.7)
  25  timeliness: full marks if that belief reached 0.7 in the first week the data showed the
      cause (distribution first fell), losing an equal share for each week later
  20  minus 10 per wrong belief still active at the end
  10  minus 5 per belief on a non-causal metric (units or revenue)
  10  minus 5 per duplicate belief (created while another active belief had the same region + metric)
"""
from agent import TRUE_CAUSE_METRIC, TRUE_CAUSE_MIN_CONFIDENCE, TRUE_CAUSE_REGION, found_true_cause
from analysis import get_metric_series
from memory import ACTIVE, get_beliefs

NON_CAUSAL_METRICS = {"units", "revenue"}


def _retired_week(belief):
    for h in belief["history"]:
        if h["new_status"] != ACTIVE:
            return h["week"]
    return None


def _first_confident_week(beliefs):
    weeks = [h["week"] for b in beliefs
             if b["region"] == TRUE_CAUSE_REGION and b["metric"] == TRUE_CAUSE_METRIC
             for h in b["history"] if h["new_confidence"] >= TRUE_CAUSE_MIN_CONFIDENCE]
    return min(weeks) if weeks else None


def _cause_first_visible_week(last_week):
    series = get_metric_series(TRUE_CAUSE_REGION, TRUE_CAUSE_METRIC, last_week)
    baseline = series[0]["value"]
    return next((p["week"] for p in series if p["value"] < baseline), None)


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


def score_run(last_week):
    beliefs = get_beliefs()
    found = found_true_cause()
    first_week = _first_confident_week(beliefs)
    visible_week = _cause_first_visible_week(last_week)
    wrong_active = sum(1 for b in beliefs if b["status"] == ACTIVE and not (
        b["region"] == TRUE_CAUSE_REGION and b["metric"] == TRUE_CAUSE_METRIC))
    non_causal = sum(1 for b in beliefs if b["metric"] in NON_CAUSAL_METRICS)
    duplicates = _duplicates(beliefs)

    if first_week is None or visible_week is None:
        timeliness = 0.0
    else:
        span = last_week - visible_week + 1
        timeliness = 25 * max(0.0, 1 - max(0, first_week - visible_week) / span)

    points = {
        "true_cause_found": 35 if found else 0,
        "timeliness": round(timeliness, 1),
        "no_wrong_active": max(0, 20 - 10 * wrong_active),
        "no_non_causal": max(0, 10 - 5 * non_causal),
        "no_duplicates": max(0, 10 - 5 * duplicates),
    }
    return {
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
