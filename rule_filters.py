"""Checks that keep playbook rules general (no hard-coded answers).

Used when the reflector proposes rules, and when choosing which saved versions are eligible
to be the "best" agent playbook.
"""
import re

REGIONS_PATTERN = re.compile(r"\b(North|South|East|West)\b", re.I)
# A specific metric (by field name or plain word) ...
METRIC_PATTERN = re.compile(
    r"\b(avg_price|distribution_points|promo_active|units|revenue|price|pricing|distribution|"
    r"stores?|promos?|promotions?)\b", re.I)
# ... together with a numeric threshold, e.g. "more than 10%", "> 0.15", "below 500".
THRESHOLD_PATTERN = re.compile(r"\d+(\.\d+)?\s*(%|percent|pp\b)|[<>≤≥]\s*\d|\b(more|less|fewer|greater|"
                               r"higher|lower|above|below|over|under|exceeds?|at least|at most)\s+(than\s+)?"
                               r"[+-]?\d", re.I)


def rule_violation(text):
    """Why a rule is too specific, or None if it passes."""
    if REGIONS_PATTERN.search(text):
        return "names a region"
    if METRIC_PATTERN.search(text) and THRESHOLD_PATTERN.search(text):
        return "metric + numeric threshold"
    return None


def playbook_violations(playbook):
    """List of 'rule N: reason' strings for rules in `playbook` that fail the filters."""
    return [f"rule {i}: {why}" for i, r in enumerate(playbook.get("rules", []), 1)
            if (why := rule_violation(r))]
