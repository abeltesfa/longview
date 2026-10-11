"""Thin wrapper around the Anthropic SDK for JSON-only requests."""
import json
import logging
import os
import re
import uuid

import anthropic
import raindrop.analytics as raindrop
from dotenv import load_dotenv

load_dotenv()

# Raindrop observability: one event per ask_json call. The SDK flushes on exit by itself.
# raindrop-ai 0.0.19 calls logging.basicConfig(INFO) on import; keep httpx from logging every request.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("raindrop.analytics").setLevel(logging.WARNING)  # failures still log as errors
raindrop.write_key = os.getenv("RAINDROP_WRITE_KEY")
# Every LLM call made by one process (a run, demo, or improve loop) shares this thread id.
SESSION_ID = f"session-{uuid.uuid4().hex[:12]}"

MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-5-5")

_client = None

# Running totals, so callers can report how many calls a run made.
stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

# Where per-call usage lines go; replace (e.g. with a no-op) to change how they appear.
usage_log = print


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


JSON_RULE = "Respond with a single valid JSON object and nothing else: no prose, no markdown fences."


def _call(system, user):
    response = _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=f"{system}\n\n{JSON_RULE}",
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium"},
        # If a safety classifier declines, the API retries on a fallback model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    u = response.usage
    stats["calls"] += 1
    stats["input_tokens"] += u.input_tokens
    stats["output_tokens"] += u.output_tokens
    usage_log(f"    [llm {response.model}] tokens in={u.input_tokens} out={u.output_tokens}")
    if response.stop_reason == "refusal":
        raise RuntimeError("LLM declined the request")
    text = "".join(b.text for b in response.content if b.type == "text")
    return text, {"model": response.model, "input_tokens": u.input_tokens,
                  "output_tokens": u.output_tokens, "stop_reason": response.stop_reason}


def _parse(text):
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Tolerate stray prose around the object before paying for a retry.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _track(event, playbook_version, system, user, output, meta, retried, error=None):
    """Send one Raindrop event for an ask_json call. Never raises."""
    if not raindrop.write_key:
        return
    try:
        if playbook_version is None:
            from playbook import get_active_playbook
            playbook_version = get_active_playbook()["version"]
        properties = {
            "system_prompt": system,
            "playbook_version": int(playbook_version),
            "json_retry": retried,
            "input_tokens": meta.get("input_tokens", 0),
            "output_tokens": meta.get("output_tokens", 0),
            "stop_reason": meta.get("stop_reason") or "",
            "db": os.environ.get("LONGVIEW_DB", "longview"),
        }
        if error:
            properties["error"] = error
        raindrop.track_ai(
            user_id=f"playbook-v{playbook_version}",
            event=event,
            model=meta.get("model", MODEL),
            user_input=user,
            output=output,
            convo_id=SESSION_ID,
            properties=properties,
        )
    except Exception as e:  # observability must never break the agent
        usage_log(f"    [raindrop] tracking failed: {e}")


def track_run_scored(playbook_version, score_breakdown, source, extra=None):
    """Send a Raindrop `run_scored` event with a run's score breakdown. Never raises."""
    if not raindrop.write_key:
        return
    try:
        properties = {
            "score": score_breakdown["score"],
            "found_true_cause": score_breakdown["found_true_cause"],
            "true_cause_confident_week": score_breakdown["true_cause_confident_week"] or 0,
            "wrong_active_beliefs": score_breakdown["wrong_active_beliefs"],
            "non_causal_beliefs": score_breakdown["non_causal_beliefs"],
            "duplicate_beliefs": score_breakdown["duplicate_beliefs"],
            "total_beliefs": score_breakdown["total_beliefs"],
            "playbook_version": int(playbook_version),
            "session_id": SESSION_ID,
            "source": source,
            **{f"points_{k}": v for k, v in score_breakdown["points"].items()},
            **(extra or {}),
        }
        raindrop.track(user_id=f"playbook-v{playbook_version}", event="run_scored", properties=properties)
    except Exception as e:
        usage_log(f"    [raindrop] tracking failed: {e}")


def ask_json(system, user, event="llm_json", playbook_version=None):
    """Ask for a JSON object; retry once with a stricter reminder if parsing fails.

    `event` names the Raindrop event; `playbook_version` is its user id (defaults to the active one).
    """
    meta, text, retried = {}, None, False
    try:
        text, meta = _call(system, user)
        try:
            result = _parse(text)
        except json.JSONDecodeError:
            usage_log("    [llm] response was not valid JSON, retrying once")
            retried = True
            first = meta
            text, meta = _call(system, f"{user}\n\nYour previous reply was not valid JSON. {JSON_RULE}")
            meta = {**meta, "input_tokens": first["input_tokens"] + meta["input_tokens"],
                    "output_tokens": first["output_tokens"] + meta["output_tokens"]}
            result = _parse(text)
    except Exception as e:
        _track(event, playbook_version, system, user, text, meta, retried, error=f"{type(e).__name__}: {e}")
        raise
    _track(event, playbook_version, system, user, text, meta, retried)
    return result
