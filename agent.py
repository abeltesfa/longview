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
from playbook import get_active_playbook, guard, rules_text

DROP_THRESHOLD = -5.0  # % week-over-week revenue change that triggers investigation
NEW_HYPOTHESIS_CONFIDENCE = 0.5
SUPERSEDE_CONFIDENCE = 0.6

# The cause planted in generate_data.py, used to score a run.
TRUE_CAUSE_REGION = "West"
TRUE_CAUSE_METRIC = "distribution_points"
TRUE_CAUSE_MIN_CONFIDENCE = 0.7

HYPOTHESIS_SYSTEM = """You are a sales analyst. A region's revenue is falling.
Propose 2-3 competing causal hypotheses. Each must name exactly one metric from the
provided list that would confirm or refute it. Prefer hypotheses that test different metrics.

Playbook rules (must follow):
{rules}

Return: {{"hypotheses": [{{"claim": "...", "metric": "...", "rationale": "..."}}]}}"""

DECISION_SYSTEM = """You are a sales analyst maintaining a set of beliefs about why revenue changed.
Given one belief, the data for the metric that tests it, revenue for context, past interventions,
and the belief's history, decide how the belief should change. Judge the belief on its own
metric first. Use other_driver_series only to find a replacement when the belief is refuted.
- REINFORCE: the metric data supports the claim.
- CONTRADICT: the metric data clearly refutes the claim and you have no better explanation.
- SUPERSEDE: the data refutes the claim AND points to a better explanation; give it as new_claim,
  with new_metric naming the metric (from the list) that would test it.
- NO_CHANGE: the data is inconclusive.
Base the decision on the numbers. Keep reason to one or two sentences citing them.

Playbook rules (must follow, including for any new_claim):
{rules}

Return: {{"action": "REINFORCE|CONTRADICT|SUPERSEDE|NO_CHANGE", "reason": "...",
"new_claim": "... (SUPERSEDE only)", "new_metric": "... (SUPERSEDE only)"}}"""


def _fmt_series(series):
    return "  ".join(f"wk{p['week']}={p['value']:,}" if not isinstance(p["value"], bool)
                     else f"wk{p['week']}={'Y' if p['value'] else 'n'}" for p in series)


def _silent(*args, **kwargs):
    pass


def _allowed_metrics(playbook):
    """Metrics a belief may test: causal ones only if that guardrail is on, else any metric."""
    if guard(playbook, "causal_metrics_only"):
        return playbook["causal_metrics"]
    return METRICS


def _active_metrics(region, exclude_id=None):
    return {b["metric"] for b in get_beliefs(region=region, status=ACTIVE) if b["_id"] != exclude_id}


def _record_promos(week, say, events):
    for region in promo_regions(week):
        if any(i["week"] == week for i in get_interventions(region)):
            continue
        description = f"Promo active in {region} in week {week}"
        record_intervention(region, week, description)
        say(f"  intervention recorded: {region} promo in week {week}")
        events.append({"type": "intervention", "region": region, "description": description})


def _propose_hypotheses(region, week, playbook, say, events):
    revenue = get_metric_series(region, "revenue", week)
    allowed = _allowed_metrics(playbook)
    result = ask_json(HYPOTHESIS_SYSTEM.format(rules=rules_text(playbook)), json.dumps({
        "region": region,
        "weekly_revenue": revenue,
        "available_metrics": allowed,
    }))
    for h in result.get("hypotheses", [])[:3]:
        if h.get("metric") not in allowed:
            say(f"    skipped hypothesis with disallowed metric {h.get('metric')!r}: {h.get('claim')}")
            continue
        if guard(playbook, "no_duplicate_metric_beliefs") and h["metric"] in _active_metrics(region):
            say(f"    skipped duplicate hypothesis on {h['metric']}: {h.get('claim')}")
            continue
        create_belief(region, h["metric"], h["claim"], NEW_HYPOTHESIS_CONFIDENCE, week,
                      reason=h.get("rationale", "initial hypothesis"))
        say(f"    + [{h['metric']}] {h['claim']}  (conf {NEW_HYPOTHESIS_CONFIDENCE})")
        events.append({"type": "hypothesis", "region": region, "metric": h["metric"], "claim": h["claim"]})


def _evaluate(belief, week, interventions, use_memory, playbook, say, events):
    region, metric = belief["region"], belief["metric"]
    allowed = _allowed_metrics(playbook)
    series = get_metric_series(region, metric, week)
    revenue = get_metric_series(region, "revenue", week)
    others = {m: get_metric_series(region, m, week) for m in allowed if m not in (metric, "revenue")}
    say(f"\n  Belief: \"{belief['claim']}\"  (conf {belief['confidence']:.2f})")
    say(f"    data checked [{metric}]: {_fmt_series(series)}")
    say(f"    revenue for context:  {_fmt_series(revenue)}")

    history = [{k: h[k] for k in ("week", "action", "new_confidence", "reason")}
               for h in belief["history"]] if use_memory else []
    decision = ask_json(DECISION_SYSTEM.format(rules=rules_text(playbook)), json.dumps({
        "region": region,
        "current_week": week,
        "claim": belief["claim"],
        "metric": metric,
        "metric_series": series,
        "revenue_series": revenue,
        "other_driver_series": others,
        "interventions": [{"week": i["week"], "description": i["description"]} for i in interventions],
        "belief_history": history,
        "available_metrics": allowed,
    }))

    action = str(decision.get("action", "NO_CHANGE")).upper()
    reason = decision.get("reason", "")
    old_conf = belief["confidence"]
    bid = belief["_id"]
    event = {"type": "decision", "region": region, "claim": belief["claim"], "metric": metric,
             "series": series, "action": action, "reason": reason, "old_confidence": old_conf}

    if action == "REINFORCE":
        event["new_confidence"] = reinforce(bid, week, reason)
        say(f"    → REINFORCE  conf {old_conf:.2f} → {event['new_confidence']:.2f}")
    elif action == "CONTRADICT":
        event["new_confidence"] = contradict(bid, week, reason)
        say(f"    → CONTRADICT  conf {old_conf:.2f} → {event['new_confidence']:.2f}")
    elif action == "SUPERSEDE" and decision.get("new_claim"):
        new_metric = decision.get("new_metric")
        if new_metric not in allowed:
            new_metric = None
        if (guard(playbook, "no_duplicate_metric_beliefs")
                and (new_metric or metric) in _active_metrics(region, exclude_id=bid)):
            # Another active belief already covers that driver: retire this one instead of duplicating.
            event.update(action="CONTRADICT", new_confidence=contradict(bid, week, reason))
            say(f"    → CONTRADICT (replacement already covered by an active belief)  "
                f"conf {old_conf:.2f} → {event['new_confidence']:.2f}")
            say(f"      reason: {reason}")
            events.append(event)
            return
        event["new_confidence"] = contradict(bid, week, reason)
        new = get_belief(supersede(bid, decision["new_claim"], SUPERSEDE_CONFIDENCE, week, reason,
                                   metric=new_metric))
        event.update(new_claim=new["claim"], new_metric=new["metric"])
        say(f"    → SUPERSEDE  old conf {old_conf:.2f} → superseded")
        say(f"      new belief: [{new['metric']}] \"{new['claim']}\"  (conf {new['confidence']:.2f})")
    else:
        event["action"] = "NO_CHANGE"
        event["new_confidence"] = old_conf
        say("    → NO_CHANGE")
    say(f"      reason: {reason}")
    events.append(event)


def process_week(week, use_memory=True, verbose=True):
    """Run one weekly cycle and return a list of event dicts describing what happened.

    With use_memory=False, prior beliefs and history are ignored. With verbose=False,
    nothing is printed (the caller renders the events instead).
    """
    say = print if verbose else _silent
    events = []
    say(f"\n{'=' * 70}\nWEEK {week}\n{'=' * 70}")
    playbook = get_active_playbook()

    summary = region_week_summary(week)
    events.append({"type": "summary", "regions": summary})
    for s in summary:
        pct = "n/a" if s["pct_change"] is None else f"{s['pct_change']:+.1f}%"
        say(f"  {s['region']:<6} revenue {s['revenue']:>10,.0f}  ({pct} WoW)")

    _record_promos(week, say, events)

    flagged = [s for s in summary if s["pct_change"] is not None and s["pct_change"] < DROP_THRESHOLD]
    if not flagged:
        say("\n  No regions flagged.")
        return events

    for s in flagged:
        region = s["region"]
        say(f"\n--- FLAGGED: {region} revenue {s['pct_change']:+.1f}% WoW ---")
        events.append({"type": "flag", "region": region, "pct_change": s["pct_change"]})

        beliefs = get_beliefs(region=region, status=ACTIVE)
        if not use_memory:
            beliefs = [b for b in beliefs if b["created_week"] == week]
        interventions = get_interventions(region) if use_memory else []

        if not beliefs:
            say("  No active beliefs; asking LLM for hypotheses:")
            _propose_hypotheses(region, week, playbook, say, events)
            beliefs = [b for b in get_beliefs(region=region, status=ACTIVE) if b["created_week"] == week]
        else:
            say(f"  {len(beliefs)} active belief(s) in memory; testing against new data.")

        for belief in beliefs:
            _evaluate(belief, week, interventions, use_memory, playbook, say, events)
    return events


def found_true_cause():
    """True if an active West belief tests distribution_points with confidence >= 0.7."""
    return any(
        b["metric"] == TRUE_CAUSE_METRIC and b["confidence"] >= TRUE_CAUSE_MIN_CONFIDENCE
        for b in get_beliefs(region=TRUE_CAUSE_REGION, status=ACTIVE)
    )
