"""Versioned analysis playbook: the rules and guardrails the agent follows.

Exactly one document in the `playbook` collection is active at a time. Every revision is a
new version; older versions are kept. Each version records its author ("human" or "agent"),
and agent-written versions record the parent_version they were derived from.

Parameters are the numeric knobs of belief updating, clamped to PARAMETER_RANGES so an
agent-written version cannot break the system.

Guardrails are code-enforced checks that only apply when their flag is on:
- causal_metrics_only: hypotheses (and replacement beliefs) may only test causal_metrics.
- no_duplicate_metric_beliefs: never hold two active beliefs on the same region + metric.
"""
from datetime import datetime, timezone

from db import get_db

CAUSAL_METRICS = ["avg_price", "distribution_points", "promo_active"]
GUARDRAILS = ["causal_metrics_only", "no_duplicate_metric_beliefs"]

DEFAULT_PARAMETERS = {
    "initial_confidence": 0.5,   # confidence of a newly proposed hypothesis
    "reinforce_boost": 0.15,     # confidence added on supporting evidence
    "contradict_penalty": 0.4,   # confidence removed on contradicting evidence
    "drop_threshold": 0.05,      # flag a region when revenue falls more than this share week over week
}
# Ranges keep self-edits safe. initial_confidence stays below the 0.7 "confident" bar, so
# confidence above it always has to be earned with evidence.
PARAMETER_RANGES = {
    "initial_confidence": (0.3, 0.65),
    "reinforce_boost": (0.05, 0.3),
    "contradict_penalty": (0.1, 0.6),
    "drop_threshold": (0.02, 0.10),
}

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
    """Make sure the human versions exist and carry author/guardrail/parameter fields."""
    for version, spec in HUMAN_VERSIONS.items():
        existing = _playbook().find_one({"version": version})
        if existing is None:
            _playbook().insert_one({
                "version": version, "active": False, "author": "human", "parent_version": None,
                "rules": spec["rules"], "causal_metrics": CAUSAL_METRICS, "guardrails": spec["guardrails"],
                "parameters": dict(DEFAULT_PARAMETERS), "note": spec["note"], "changes": [], "created_at": datetime.now(timezone.utc),
            })
        else:
            fill = {k: v for k, v in {"author": "human", "guardrails": spec["guardrails"],
                                      "parameters": dict(DEFAULT_PARAMETERS),
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


def clamp_parameters(values):
    """Return a full parameter dict: defaults overridden by `values`, each clamped to its range."""
    out = {}
    for name, default in DEFAULT_PARAMETERS.items():
        lo, hi = PARAMETER_RANGES[name]
        try:
            v = float(values.get(name, default))
        except (TypeError, ValueError):
            v = default
        out[name] = round(min(hi, max(lo, v)), 3)
    return out


def params(playbook):
    """The playbook's parameters (versions saved before parameters existed get the defaults)."""
    return clamp_parameters(playbook.get("parameters") or {})


def revise_playbook(rules, guardrails, author, parent_version=None, changes=None, note="", activate=False,
                    parameters=None):
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
        "parameters": clamp_parameters(parameters or {}),
        "changes": list(changes or []),
        "note": note,
        "created_at": datetime.now(timezone.utc),
    })
    if activate:
        set_active_version(version)
    return version


def guard(playbook, name):
    return bool(playbook.get("guardrails", {}).get(name, False))


def version_scores():
    """{version: latest scored run doc} from the runs collection."""
    latest = {}
    for r in get_db()["runs"].find({"score": {"$exists": True}}).sort("timestamp", 1):
        latest[r["playbook_version"]] = r
    return latest


def excluded_agent_versions():
    """{version: reason} for agent versions that can't be "best": rejected, or rules failing the filters."""
    from rule_filters import playbook_violations  # local import: rule_filters is standalone, keep playbook light
    out = {}
    for p in _playbook().find({"author": "agent"}):
        if p.get("accepted") is False:
            out[p["version"]] = "rejected by improve loop"
        elif violations := playbook_violations(p):
            out[p["version"]] = "fails rule filters (" + "; ".join(violations) + ")"
    return out


def best_agent_version():
    """Highest-scoring eligible agent version (newest wins ties), or None. See excluded_agent_versions."""
    scores = version_scores()
    excluded = excluded_agent_versions()
    agent_versions = [p["version"] for p in _playbook().find({"author": "agent"}) if p["version"] not in excluded]
    scored = [(scores[v]["score"], v) for v in agent_versions if v in scores]
    return max(scored)[1] if scored else None


def rules_text(playbook):
    return "\n".join(f"- {r}" for r in playbook["rules"]) or "(no rules)"
