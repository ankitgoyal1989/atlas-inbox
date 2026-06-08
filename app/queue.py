# app/queue.py — the approval queue (the human-in-the-loop gate).
#
# This is where proposals become reality — only on explicit approval. The gated
# Google calls (send_draft / create_event) live ONLY here.
import json

from . import calendar, gmail
from .config import settings
from .db import pg
from .guardrails import confirm_recipient


def _readable_error(kind: str, exc: Exception) -> str:
    """Turn raw Google/API errors into something the user can act on."""
    text = str(exc)
    if "insufficient authentication scopes" in text or "Insufficient Permission" in text:
        scope = "calendar.events" if kind == "create_event" else "gmail.send"
        return (
            f"This account's OAuth grant is missing the '{scope}' scope, so the "
            f"{kind} action can't run. Re-connect at /oauth/start and grant the "
            "new permission, then approve again. (The action is still pending.)"
        )
    return f"failed to execute {kind}: {text}"


def enqueue(kind: str, payload: dict) -> dict:
    """Record a proposed action awaiting approval. Touches no external system."""
    with pg() as cur:
        cur.execute(
            "INSERT INTO pending_actions (kind, payload) VALUES (%s, %s) RETURNING id",
            (kind, json.dumps(payload)),
        )
        new_id = cur.fetchone()[0]
    return {"queued": True, "id": str(new_id), "kind": kind}


def list_pending() -> list[dict]:
    with pg() as cur:
        cur.execute(
            "SELECT id, kind, payload FROM pending_actions "
            "WHERE status = 'pending' ORDER BY created_at"
        )
        return [
            {"id": str(i), "kind": k, "payload": p} for i, k, p in cur.fetchall()
        ]


def approve(user_id: str, action_id: str, edits: dict | None = None) -> dict:
    """Execute the real-world action — the ONLY place send/create happen.

    `edits` carries any last-minute changes the user made in the approval UI
    (currently the email body). They are written to the Gmail draft *before*
    sending, so what the user sees is what actually goes out.
    """
    user_id = user_id or settings().default_user_id
    edits = edits or {}
    with pg() as cur:
        cur.execute(
            "SELECT kind, payload FROM pending_actions "
            "WHERE id = %s AND status = 'pending'",
            (action_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"error": "not found or already decided"}

    kind, payload = row
    # Execute the gated action. Any failure (guardrail block, missing OAuth
    # scope, Google API error) is returned as a structured error and the action
    # is LEFT pending so the user can fix the cause and retry — never a 500.
    try:
        if kind == "send_email":
            # Guardrail: recipient must be a trusted thread participant.
            confirm_recipient(
                payload["to"], user_id=user_id, thread_id=payload.get("thread_id")
            )
            # Apply the user's edited body to the draft before sending, if changed.
            edited_body = edits.get("body")
            if edited_body is not None and edited_body != payload.get("body"):
                gmail.update_draft(
                    user_id,
                    payload["draft_id"],
                    payload["to"],
                    payload.get("subject", ""),
                    edited_body,
                    payload.get("thread_id"),
                )
                payload["body"] = edited_body
            result = gmail.send_draft(user_id, payload["draft_id"])
        elif kind == "create_event":
            result = calendar.create_event(
                user_id,
                payload["summary"],
                payload["start"],
                payload["end"],
                payload["attendees"],
            )
        else:
            return {"error": f"unknown action kind: {kind}"}
    except PermissionError as exc:
        return {"error": f"blocked by guardrail: {exc}", "action_id": action_id}
    except Exception as exc:
        return {"error": _readable_error(kind, exc), "action_id": action_id}

    with pg() as cur:
        cur.execute(
            "UPDATE pending_actions SET status = 'executed', decided_at = now() "
            "WHERE id = %s",
            (action_id,),
        )
    return {"executed": kind, "result": result}


def reject(action_id: str) -> dict:
    with pg() as cur:
        cur.execute(
            "UPDATE pending_actions SET status = 'rejected', decided_at = now() "
            "WHERE id = %s",
            (action_id,),
        )
    return {"rejected": action_id}
