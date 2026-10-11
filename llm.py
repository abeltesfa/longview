"""Thin wrapper around the Anthropic SDK for JSON-only requests."""
import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

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
    return "".join(b.text for b in response.content if b.type == "text")


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


def ask_json(system, user):
    """Ask for a JSON object; retry once with a stricter reminder if parsing fails."""
    text = _call(system, user)
    try:
        return _parse(text)
    except json.JSONDecodeError:
        usage_log("    [llm] response was not valid JSON, retrying once")
        text = _call(system, f"{user}\n\nYour previous reply was not valid JSON. {JSON_RULE}")
        return _parse(text)
