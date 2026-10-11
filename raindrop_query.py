"""Read a run's LLM events back from the Raindrop Query API (plain HTTP, works on Python 3.9).

Needs RAINDROP_QUERY_API_KEY (a Query API key from https://auth.raindrop.ai/org/api_keys; the
write key cannot read). Without it, fetch_run_events returns None and a reason.
"""
import os
import time
from collections import Counter

import raindrop.analytics as raindrop
import requests

QUERY_URL = "https://query.raindrop.ai/v1/events"
MAX_OUTPUT_CHARS = 300
MAX_DECISIONS = 12


def fetch_run_events(run_doc, attempts=3, wait_seconds=3):
    """Return (events, None) for the run's playbook user + session since it started, or (None, reason)."""
    key = os.getenv("RAINDROP_QUERY_API_KEY")
    if not key:
        return None, "RAINDROP_QUERY_API_KEY is not set"
    if not run_doc.get("session_id"):
        return None, "run has no session_id (recorded before Raindrop tracking)"
    raindrop.flush()  # make sure this process's events have been sent before reading them back
    params = {
        "user_id": f"playbook-v{run_doc['playbook_version']}",
        "convo_id": run_doc["session_id"],
        "timestamp[gte]": run_doc["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 100,
    }
    for attempt in range(attempts):
        try:
            resp = requests.get(QUERY_URL, params=params, timeout=15,
                                headers={"Authorization": f"Bearer {key}"})
        except requests.RequestException as e:
            return None, f"request failed: {e}"
        if resp.status_code != 200:
            return None, f"Query API returned HTTP {resp.status_code}"
        events = resp.json().get("data", [])
        if events:
            return events, None
        if attempt + 1 < attempts:
            time.sleep(wait_seconds)  # freshly sent events can take a few seconds to become queryable
    return None, "no events found for this run yet"


def summarize_events(events):
    """Compact evidence for the reflector: counts, cost, retries/errors, and recent decision outputs."""
    props = [e.get("properties") or {} for e in events]
    decisions = [e for e in events if e.get("event_name") == "belief_decision"]
    decisions.sort(key=lambda e: e.get("timestamp", ""))
    return {
        "event_counts": dict(Counter(e.get("event_name") for e in events)),
        "total_input_tokens": sum(int(p.get("input_tokens", 0) or 0) for p in props),
        "total_output_tokens": sum(int(p.get("output_tokens", 0) or 0) for p in props),
        "json_retries": sum(1 for p in props if p.get("json_retry") in (True, "true")),
        "errors": [p["error"] for p in props if p.get("error")],
        "signals": [s for e in events for s in (e.get("signals") or [])],
        "decision_outputs": [(e.get("assistant_output") or "")[:MAX_OUTPUT_CHARS]
                             for e in decisions[:MAX_DECISIONS]],
    }
