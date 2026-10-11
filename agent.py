"""Weekly agent: flag revenue drops, form hypotheses, test them against data, update memory."""
import json

from analysis import METRICS, get_metric_series, promo_regions, region_week_summary
from llm import ask_json
from memory import (
    ACTIVE,
    contradict,
    create_belief,
    get_belief,
    get_beliefs,
    get_interventions,
    record_intervention,
    reinforce,
    supersede,
)

DROP_THRESHOLD = -5.0  # % week-over-week revenue change that triggers investigation
NEW_HYPOTHESIS_CONFIDENCE = 0.5
SUPERSEDE_CONFIDENCE = 0.6

HYPOTHESIS_SYSTEM = """You are a sales analyst. A region's revenue is falling.
Propose 2-3 competing causal hypotheses. Each must name exactly one metric from the
provided list that would confirm or refute it. Prefer hypotheses that test different metrics.
Return: {"hypotheses": [{"claim": "...", "metric": "...", "rationale": "..."}]}"""

DECISION_SYSTEM = """You are a sales analyst maintaining a set of beliefs about why revenue changed.
Given one belief, the data for the metric that tests it, revenue for context, past interventions,
and the belief's history, decide how the belief should change:
- REINFORCE: the metric data supports the claim.
- CONTRADICT: the metric data clearly refutes the claim and you have no better explanation.
- SUPERSEDE: the data refutes the claim AND points to a better explanation; give it as new_claim,
  with new_metric naming the metric (from the list) that would test it.
- NO_CHANGE: the data is inconclusive.
Base the decision on the numbers. Keep reason to one or two sentences citing them.
Return: {"action": "REINFORCE|CONTRADICT|SUPERSEDE|NO_CHANGE", "reason": "...",
"new_claim": "... (SUPERSEDE only)", "new_metric": "... (SUPERSEDE only)"}"""


def _fmt_series(series):
    return "  ".join(f"wk{p['week']}={p['value']:,}" if not isinstance(p["value"], bool)
                     else f"wk{p['week']}={'Y' if p['value'] else 'n'}" for p in series)


def _record_promos(week):
    for region in promo_regions(week):
        if any(i["week"] == week for i in get_interventions(region)):
            continue
        record_intervention(region, week, f"Promo active in {region} in week {week}")
        print(f"  intervention recorded: {region} promo in week {week}")


def _propose_hypotheses(region, week):
    revenue = get_metric_series(region, "revenue", week)
    result = ask_json(HYPOTHESIS_SYSTEM, json.dumps({
        "region": region,
        "weekly_revenue": revenue,
        "available_metrics": METRICS,
    }))
    created = []
    for h in result.get("hypotheses", [])[:3]:
        if h.get("metric") not in METRICS:
            print(f"    skipped hypothesis with unknown metric {h.get('metric')!r}: {h.get('claim')}")
            continue
        bid = create_belief(region, h["metric"], h["claim"], NEW_HYPOTHESIS_CONFIDENCE, week,
                            reason=h.get("rationale", "initial hypothesis"))
        print(f"    + [{h['metric']}] {h['claim']}  (conf {NEW_HYPOTHESIS_CONFIDENCE})")
        created.append(bid)
    return created


def _evaluate(belief, week, interventions, use_memory):
    region, metric = belief["region"], belief["metric"]
    series = get_metric_series(region, metric, week)
    revenue = get_metric_series(region, "revenue", week)
    print(f"\n  Belief: \"{belief['claim']}\"  (conf {belief['confidence']:.2f})")
    print(f"    data checked [{metric}]: {_fmt_series(series)}")
    if metric != "revenue":
        print(f"    revenue for context:  {_fmt_series(revenue)}")

    history = [{k: h[k] for k in ("week", "action", "new_confidence", "reason")}
               for h in belief["history"]] if use_memory else []
    decision = ask_json(DECISION_SYSTEM, json.dumps({
        "current_week": week,
        "claim": belief["claim"],
        "metric": metric,
        "metric_series": series,
        "revenue_series": revenue,
        "interventions": [{"week": i["week"], "description": i["description"]} for i in interventions],
        "belief_history": history,
        "available_metrics": METRICS,
    }))

    action = str(decision.get("action", "NO_CHANGE")).upper()
    reason = decision.get("reason", "")
    old_conf = belief["confidence"]
    bid = belief["_id"]

    if action == "REINFORCE":
        new_conf = reinforce(bid, week, reason)
        print(f"    → REINFORCE  conf {old_conf:.2f} → {new_conf:.2f}")
    elif action == "CONTRADICT":
        new_conf = contradict(bid, week, reason)
        print(f"    → CONTRADICT  conf {old_conf:.2f} → {new_conf:.2f}")
    elif action == "SUPERSEDE" and decision.get("new_claim"):
        new_metric = decision.get("new_metric")
        if new_metric not in METRICS:
            new_metric = None
        contradict(bid, week, reason)
        new_id = supersede(bid, decision["new_claim"], SUPERSEDE_CONFIDENCE, week, reason, metric=new_metric)
        new = get_belief(new_id)
        print(f"    → SUPERSEDE  old conf {old_conf:.2f} → superseded")
        print(f"      new belief: [{new['metric']}] \"{new['claim']}\"  (conf {new['confidence']:.2f})")
    else:
        print("    → NO_CHANGE")
    print(f"      reason: {reason}")


def process_week(week, use_memory=True):
    """Run one weekly cycle. With use_memory=False, prior beliefs and history are ignored."""
    print(f"\n{'=' * 70}\nWEEK {week}\n{'=' * 70}")

    summary = region_week_summary(week)
    for s in summary:
        pct = "n/a" if s["pct_change"] is None else f"{s['pct_change']:+.1f}%"
        print(f"  {s['region']:<6} revenue {s['revenue']:>10,.0f}  ({pct} WoW)")

    _record_promos(week)

    flagged = [s for s in summary if s["pct_change"] is not None and s["pct_change"] < DROP_THRESHOLD]
    if not flagged:
        print("\n  No regions flagged.")
        return

    for s in flagged:
        region = s["region"]
        print(f"\n--- FLAGGED: {region} revenue {s['pct_change']:+.1f}% WoW ---")

        beliefs = get_beliefs(region=region, status=ACTIVE)
        if not use_memory:
            beliefs = [b for b in beliefs if b["created_week"] == week]
        interventions = get_interventions(region) if use_memory else []

        if not beliefs:
            print("  No active beliefs; asking LLM for hypotheses:")
            _propose_hypotheses(region, week)
            beliefs = [b for b in get_beliefs(region=region, status=ACTIVE) if b["created_week"] == week]
        else:
            print(f"  {len(beliefs)} active belief(s) in memory; testing against new data.")

        for belief in beliefs:
            _evaluate(belief, week, interventions, use_memory)
