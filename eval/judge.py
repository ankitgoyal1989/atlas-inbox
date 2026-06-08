# eval/judge.py — LLM-as-judge for draft quality, plus deterministic slot checks.
import json

from openai import OpenAI

from app.config import settings

_JUDGE_PROMPT = """You are grading an email draft produced by an assistant.

Grade it against ALL of these criteria:
{criteria}

Additionally it must be: on-topic, faithful to the thread, matching the user's
style, and free of fabricated commitments, dates, names, or facts.

THREAD:
{thread}

USER REQUEST:
{request}

DRAFT:
{draft}

Respond with strict JSON: {{"pass": true|false, "reasons": "<one sentence>"}}.
"""


def judge_draft(thread: str, request: str, draft: str, criteria: list[str]) -> dict:
    """Return {"pass": bool, "reasons": str} for a single draft case."""
    client = OpenAI(api_key=settings().openai_api_key)
    prompt = _JUDGE_PROMPT.format(
        criteria="\n".join(f"- {c}" for c in criteria),
        thread=thread,
        request=request,
        draft=draft,
    )
    resp = client.chat.completions.create(
        model=settings().chat_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        verdict = json.loads(resp.choices[0].message.content)
        verdict["pass"] = bool(verdict.get("pass"))
        return verdict
    except Exception:
        return {"pass": False, "reasons": "unparseable judge response"}


def slot_is_valid(proposed, busy_blocks: list[dict]) -> bool:
    """Objective check: at least one proposed slot conflicts with nothing.

    `proposed` may be the agent's free-text output or a list of {start, end}.
    We extract candidate slots and verify each against the known busy blocks.
    """
    slots = _extract_slots(proposed)
    if not slots:
        return False
    from app.calendar import _overlaps_any, _parse  # reuse the same logic

    intervals = [(_parse(b["start"]), _parse(b["end"])) for b in busy_blocks]
    return any(
        not _overlaps_any(_parse(s["start"]), _parse(s["end"]), intervals)
        for s in slots
    )


def _extract_slots(proposed) -> list[dict]:
    if isinstance(proposed, list):
        return [s for s in proposed if isinstance(s, dict) and "start" in s]
    # Try to find embedded JSON in a string output.
    if isinstance(proposed, str):
        import re

        for match in re.findall(r"\{[^{}]*\"start\"[^{}]*\}", proposed):
            try:
                obj = json.loads(match)
                if "start" in obj and "end" in obj:
                    return [obj]
            except Exception:
                continue
    return []
