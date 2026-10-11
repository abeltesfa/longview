"""Belief memory for longview: what the system currently thinks, and how it got there.

Beliefs live in the `beliefs` collection; every change appends to the belief's
`history` so its evolution can be replayed. Interventions (actions taken in the
real world, like a promo) live in a separate `interventions` collection.
"""
from bson import ObjectId

from db import get_db

ACTIVE = "active"
CONTRADICTED = "contradicted"
SUPERSEDED = "superseded"

MAX_CONFIDENCE = 0.99
MIN_CONFIDENCE = 0.01


def _beliefs():
    return get_db()["beliefs"]


def _interventions():
    return get_db()["interventions"]


def _clamp(x):
    return round(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, x)), 2)


def _get(belief_id):
    belief = _beliefs().find_one({"_id": ObjectId(belief_id)})
    if belief is None:
        raise ValueError(f"No belief with id {belief_id}")
    return belief


def _history_entry(week, action, old, new_status, new_confidence, reason):
    return {
        "week": week,
        "action": action,
        "old_status": old["status"] if old else None,
        "new_status": new_status,
        "old_confidence": old["confidence"] if old else None,
        "new_confidence": new_confidence,
        "reason": reason,
    }


def create_belief(region, metric, claim, confidence, week, reason, supersedes=None):
    """Insert a new active belief and return its id."""
    confidence = _clamp(confidence)
    doc = {
        "region": region,
        "metric": metric,
        "claim": claim,
        "status": ACTIVE,
        "confidence": confidence,
        "times_reinforced": 0,
        "evidence": [],
        "history": [_history_entry(week, "created", None, ACTIVE, confidence, reason)],
        "created_week": week,
        "updated_week": week,
        "superseded_by": None,
        "supersedes": supersedes,
    }
    return _beliefs().insert_one(doc).inserted_id


def get_beliefs(region=None, status=None):
    """Return beliefs, optionally filtered by region and/or status, oldest first."""
    query = {}
    if region:
        query["region"] = region
    if status:
        query["status"] = status
    return list(_beliefs().find(query).sort([("created_week", 1), ("_id", 1)]))


def get_belief(belief_id):
    return _get(belief_id)


def reinforce(belief_id, week, evidence_summary, boost=0.15):
    """New supporting evidence: raise confidence (capped at 0.99) and count it."""
    old = _get(belief_id)
    if old["status"] != ACTIVE:
        raise ValueError(f"Cannot reinforce a {old['status']} belief")
    new_conf = _clamp(old["confidence"] + boost)
    _beliefs().update_one(
        {"_id": old["_id"]},
        {
            "$set": {"confidence": new_conf, "updated_week": week},
            "$inc": {"times_reinforced": 1},
            "$push": {
                "evidence": {"week": week, "summary": evidence_summary, "supports": True},
                "history": _history_entry(week, "reinforced", old, ACTIVE, new_conf, evidence_summary),
            },
        },
    )
    return new_conf


def contradict(belief_id, week, evidence_summary, penalty=0.4):
    """Evidence against the belief: mark it contradicted and drop confidence."""
    old = _get(belief_id)
    if old["status"] == SUPERSEDED:
        raise ValueError("Cannot contradict a superseded belief")
    new_conf = _clamp(old["confidence"] - penalty)
    _beliefs().update_one(
        {"_id": old["_id"]},
        {
            "$set": {"status": CONTRADICTED, "confidence": new_conf, "updated_week": week},
            "$push": {
                "evidence": {"week": week, "summary": evidence_summary, "supports": False},
                "history": _history_entry(week, "contradicted", old, CONTRADICTED, new_conf, evidence_summary),
            },
        },
    )
    return new_conf


def supersede(old_id, new_claim, confidence, week, reason, metric=None):
    """Replace a belief with a new one and link them. Returns the new id.

    The new belief keeps the old region; it keeps the old metric unless `metric` is given.
    """
    old = _get(old_id)
    if old["status"] == SUPERSEDED:
        raise ValueError("Belief is already superseded")
    new_id = create_belief(old["region"], metric or old["metric"], new_claim, confidence, week, reason,
                           supersedes=old["_id"])
    _beliefs().update_one(
        {"_id": old["_id"]},
        {
            "$set": {"status": SUPERSEDED, "superseded_by": new_id, "updated_week": week},
            "$push": {
                "history": _history_entry(week, "superseded", old, SUPERSEDED, old["confidence"],
                                          f"Replaced by: {new_claim}"),
            },
        },
    )
    return new_id


def record_intervention(region, week, description):
    return _interventions().insert_one(
        {"region": region, "week": week, "description": description}
    ).inserted_id


def get_interventions(region=None):
    query = {"region": region} if region else {}
    return list(_interventions().find(query).sort("week", 1))


def reset_memory():
    _beliefs().delete_many({})
    _interventions().delete_many({})
