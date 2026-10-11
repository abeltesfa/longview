"""Versioned analysis playbook: the rules and guardrails the agent follows.

Exactly one document in the `playbook` collection is active at a time. Every revision is a
new version; older versions are kept. Each version records its author ("human" or "agent"),
and agent-written versions record the parent_version they were derived from.

Guardrails are code-enforced checks that only apply when their flag is on:
- causal_metrics_only: hypotheses (and replacement beliefs) may only test causal_metrics.
- no_duplicate_metric_beliefs: never hold two active beliefs on the same region + metric.
"""
from datetime import datetime, timezone

from db import get_db

CAUSAL_METRICS = ["avg_price", "distribution_points", "promo_active"]
GUARDRAILS = ["causal_metrics_only", "no_duplicate_metric_beliefs"]

RULE_DRIVER_METRIC = ("Hypotheses must name a driver metric that could cause the revenue change, "
                      "never revenue or units themselves.")
RULE_BASELINE = ("Judge a belief by whether its driver metric is still off its week-1 baseline in the "
                 "direction that explains lower revenue. Replace or contradict it only once its own metric "
                 "no longer supports it (for example, the driver has returned to baseline while revenue "
                 "stays down).")
RULE_NO_DUPLICATES = "Never create a belief that duplicates another active belief's metric for the same region."

# Human-written versions. v0 is the blank-slate starting point for self-improvement.
HUMAN_VERSIONS = {
    0: {"rules": [], "guardrails": {"causal_metrics_only": False, "no_duplicate_metric_beliefs": False},
        "note": "blank slate: no rules, all guardrails off"},
    1: {"rules": [RULE_DRIVER_METRIC],
        "guardrails": {"causal_metrics_only": True, "no_duplicate_metric_beliefs": False},
        "note": "initial seed"},
    2: {"rules": [RULE_DRIVER_METRIC, RULE_BASELINE, RULE_NO_DUPLICATES],
        "guardrails": {"causal_metrics_only": True, "no_duplicate_metric_beliefs": True},
        "note": "judge beliefs against baseline; no duplicate beliefs"},
}
DEFAULT_ACTIVE_VERSION = 2


def _playbook():
    return get_db()["playbook"]


def ensure_seed_versions():
    """Make sure the human versions exist and carry author/guardrail fields."""
    for version, spec in HUMAN_VERSIONS.items():
        existing = _playbook().find_one({"version": version})
        if existing is None:
            _playbook().insert_one({
                "version": version, "active": False, "author": "human", "parent_version": None,
                "rules": spec["rules"], "causal_metrics": CAUSAL_METRICS, "guardrails": spec["guardrails"],
                "note": spec["note"], "changes": [], "created_at": datetime.now(timezone.utc),
            })
        else:
            fill = {k: v for k, v in {"author": "human", "guardrails": spec["guardrails"],
                                      "parent_version": None, "changes": []}.items() if k not in existing}
            if fill:
                _playbook().update_one({"_id": existing["_id"]}, {"$set": fill})
    if _playbook().find_one({"active": True}) is None:
        set_active_version(DEFAULT_ACTIVE_VERSION)


def get_active_playbook():
    doc = _playbook().find_one({"active": True})
    if doc is None:
        ensure_seed_versions()
        doc = _playbook().find_one({"active": True})
    return doc


def get_playbook(version):
    doc = _playbook().find_one({"version": version})
    if doc is None:
        raise ValueError(f"No playbook version {version}")
    return doc


def set_active_version(version):
    get_playbook(version)  # raises if missing
    _playbook().update_many({"active": True}, {"$set": {"active": False}})
    _playbook().update_one({"version": version}, {"$set": {"active": True}})


def revise_playbook(rules, guardrails, author, parent_version=None, changes=None, note="", activate=False):
    """Insert a new playbook version and return its number."""
    latest = _playbook().find_one(sort=[("version", -1)])
    version = (latest["version"] + 1) if latest else 0
    _playbook().insert_one({
        "version": version,
        "active": False,
        "author": author,
        "parent_version": parent_version,
        "rules": list(rules),
        "causal_metrics": CAUSAL_METRICS,
        "guardrails": {g: bool(guardrails.get(g, False)) for g in GUARDRAILS},
        "changes": list(changes or []),
        "note": note,
        "created_at": datetime.now(timezone.utc),
    })
    if activate:
        set_active_version(version)
    return version


def guard(playbook, name):
    return bool(playbook.get("guardrails", {}).get(name, False))


def rules_text(playbook):
    return "\n".join(f"- {r}" for r in playbook["rules"]) or "(no rules)"
