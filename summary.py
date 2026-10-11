"""Executive summary of a region's week, with or without the agent's memory."""
import json

from analysis import METRICS, get_metric_series
from llm import ask_json
from memory import ACTIVE, get_beliefs, get_interventions

REGION = "West"

SYSTEM = """You are briefing a sales executive. Write a 3-4 sentence executive summary of the
region's performance this week: what happened, the most likely cause, and what to do next.
Use only the information provided; if the cause is unclear from it, say so.
Return: {"summary": "..."}"""


def _week_snapshot(region, week):
    return {m: get_metric_series(region, m, week)[-1]["value"] for m in METRICS}


def summarize(week, use_memory, region=REGION):
    """Return a 3-4 sentence executive summary for `region` in `week`.

    Without memory the LLM sees only that week's numbers. With memory it also sees the
    full trend, the agent's current and retired beliefs, and recorded interventions.
    """
    if not use_memory:
        context = {"region": region, "week": week, "this_week": _week_snapshot(region, week)}
    else:
        beliefs = get_beliefs(region=region)
        context = {
            "region": region,
            "week": week,
            "trend": {m: [p["value"] for p in get_metric_series(region, m, week)] for m in METRICS},
            "active_beliefs": [
                {"claim": b["claim"], "metric": b["metric"], "confidence": b["confidence"]}
                for b in beliefs if b["status"] == ACTIVE
            ],
            "retired_beliefs": [
                {"claim": b["claim"], "status": b["status"], "confidence": b["confidence"],
                 "why": b["history"][-1]["reason"]}
                for b in beliefs if b["status"] != ACTIVE
            ],
            "interventions": [{"week": i["week"], "description": i["description"]}
                              for i in get_interventions(region)],
        }
    return ask_json(SYSTEM, json.dumps(context), event="executive_summary").get("summary", "")
