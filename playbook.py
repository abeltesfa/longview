"""Versioned analysis playbook: the rules the agent's prompts must follow.

Exactly one document in the `playbook` collection is active at a time. Revising the
playbook inserts a new version and deactivates the old one, so earlier versions remain.
"""
from datetime import datetime, timezone

from db import get_db

DEFAULT_RULES = [
    "Hypotheses must name a driver metric that could cause the revenue change, "
    "never revenue or units themselves.",
]
DEFAULT_CAUSAL_METRICS = ["avg_price", "distribution_points", "promo_active"]


def _playbook():
    return get_db()["playbook"]


def get_active_playbook():
    """Return the active playbook, seeding version 1 if none exists."""
    doc = _playbook().find_one({"active": True})
    if doc is None:
        revise_playbook(DEFAULT_RULES, DEFAULT_CAUSAL_METRICS, note="initial seed")
        doc = _playbook().find_one({"active": True})
    return doc


def revise_playbook(rules, causal_metrics, note=""):
    """Create a new active playbook version; the previous one is kept but deactivated."""
    latest = _playbook().find_one(sort=[("version", -1)])
    version = (latest["version"] + 1) if latest else 1
    _playbook().update_many({"active": True}, {"$set": {"active": False}})
    _playbook().insert_one({
        "version": version,
        "active": True,
        "rules": list(rules),
        "causal_metrics": list(causal_metrics),
        "note": note,
        "created_at": datetime.now(timezone.utc),
    })
    return version


def rules_text(playbook):
    return "\n".join(f"- {r}" for r in playbook["rules"])
