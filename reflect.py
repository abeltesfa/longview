"""Reflection: diagnose a scored run and propose a revised playbook (author: agent).

The reflector sees the score breakdown, belief histories and current playbook, never the
planted answer. Rules that name a specific region are dropped so the playbook stays general.
"""
import json
import re

from llm import ask_json
from playbook import DEFAULT_PARAMETERS, GUARDRAILS, PARAMETER_RANGES, clamp_parameters, params, revise_playbook

REGIONS_PATTERN = re.compile(r"\b(North|South|East|West)\b", re.I)
MAX_RULES = 6

GUARDRAIL_DOCS = {
    "causal_metrics_only": "Code rejects any belief whose test metric is not a causal driver "
                           "(avg_price, distribution_points, promo_active).",
    "no_duplicate_metric_beliefs": "Code refuses to create a belief when another active belief in the "
                                   "same region already tests that metric.",
}

PARAMETER_DOCS = {
    "initial_confidence": "confidence given to each newly proposed hypothesis",
    "reinforce_boost": "confidence added when evidence supports a belief",
    "contradict_penalty": "confidence removed when evidence contradicts a belief",
    "drop_threshold": "a region is investigated when weekly revenue falls by more than this fraction",
}

SCORING_DOCS = """Runs are scored 0-100 from stored beliefs and data:
- 35 if an active belief about the true driver of the decline ends with confidence >= 0.7
- 25 for timeliness: how soon after the driver first moved in the data the agent became confident in it
- 20 minus 10 per wrong belief still active at the end
- 10 minus 5 per belief that tests revenue or units (symptoms, not causes)
- 10 minus 5 per duplicate belief (same region + metric as another active belief)"""

SYSTEM = f"""You improve the playbook of a sales-analysis agent. Each week the agent flags regions
whose revenue fell, proposes causal hypotheses (beliefs), and tests each against one metric,
deciding REINFORCE / CONTRADICT / SUPERSEDE / NO_CHANGE. The playbook's rules are injected into
those prompts; guardrails are code-enforced switches; parameters are the numbers code uses to
update confidence and flag regions (rules cannot change these numbers, only parameters can).
Parameter values outside their allowed range are clamped.

{SCORING_DOCS}

Diagnose what went wrong in the run, then propose a revised playbook. Rules must be general
analytic principles that would hold for any region or dataset: never name a specific region,
week, or the answer you suspect. Prefer few, sharp rules (at most {MAX_RULES}). Keep what works.

Return: {{"diagnosis": "2-3 sentences",
 "rules": [{{"text": "...", "change": "kept|added|edited|removed", "justification": "one line"}}],
 "guardrails": {{"<name>": {{"on": true/false, "justification": "one line"}}}},
 "parameters": {{"<name>": {{"value": number, "justification": "one line"}}}}}}
Include only the parameters you want to change."""


def _compact_beliefs(run_doc):
    out = []
    for region, beliefs in run_doc["final_beliefs"].items():
        for b in beliefs:
            out.append({
                "region": region, "claim": b["claim"], "metric": b["metric"],
                "final_status": b["status"], "final_confidence": b["confidence"],
                "history": [f"wk{h['week']} {h['action']} → {h['new_status']} {h['new_confidence']:.2f}: {h['reason']}"
                            for h in b["history"]],
            })
    return out


def reflect(run_doc, playbook, rejected=None):
    """Ask the LLM for a revised playbook; save it (inactive) and return (version, proposal)."""
    payload = {
        "current_playbook": {
            "version": playbook["version"],
            "rules": playbook["rules"],
            "guardrails": {g: {"on": playbook["guardrails"].get(g, False), "what_it_does": GUARDRAIL_DOCS[g]}
                           for g in GUARDRAILS},
            "parameters": {n: {"value": v, "allowed_range": PARAMETER_RANGES[n], "what_it_does": PARAMETER_DOCS[n]}
                           for n, v in params(playbook).items()},
        },
        "score": run_doc["score"],
        "score_breakdown": {k: v for k, v in run_doc["score_breakdown"].items()},
        "beliefs": _compact_beliefs(run_doc),
    }
    if rejected:
        payload["previous_rejected_proposal"] = rejected
    proposal = ask_json(SYSTEM, json.dumps(payload, default=str), event="playbook_reflection",
                        playbook_version=playbook["version"])

    rules, changes = [], []
    for r in proposal.get("rules", []):
        text, change = r.get("text", "").strip(), r.get("change", "kept")
        why = r.get("justification", "")
        if not text:
            continue
        if change != "removed" and REGIONS_PATTERN.search(text):
            changes.append(f"dropped proposed rule (names a region): {text}")
            continue
        if change == "removed":
            changes.append(f"removed rule: {text} ({why})")
            continue
        if len(rules) >= MAX_RULES:
            changes.append(f"dropped rule over the {MAX_RULES}-rule limit: {text}")
            continue
        rules.append(text)
        if change in ("added", "edited"):
            changes.append(f"{change} rule: {text} ({why})")

    guardrails = {}
    for g in GUARDRAILS:
        old = bool(playbook["guardrails"].get(g, False))
        spec = proposal.get("guardrails", {}).get(g, {})
        new = bool(spec.get("on", old))
        guardrails[g] = new
        if new != old:
            changes.append(f"guardrail {g}: {'off → on' if new else 'on → off'} ({spec.get('justification', '')})")

    old_params = params(playbook)
    proposed = {n: spec.get("value") for n, spec in (proposal.get("parameters") or {}).items()
                if n in DEFAULT_PARAMETERS and isinstance(spec, dict)}
    new_params = clamp_parameters({**old_params, **proposed})
    for n, new in new_params.items():
        if new != old_params[n]:
            why = proposal["parameters"][n].get("justification", "")
            clamped = " (clamped)" if proposed.get(n) != new else ""
            changes.append(f"parameter {n}: {old_params[n]} → {new}{clamped} ({why})")

    version = revise_playbook(rules, guardrails, author="agent", parent_version=playbook["version"],
                              changes=changes, note=proposal.get("diagnosis", ""), parameters=new_params)
    return version, {"diagnosis": proposal.get("diagnosis", ""), "changes": changes}
