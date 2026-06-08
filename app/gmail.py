# app/gmail.py — a thin wrapper over the Gmail API.
#
# create_draft needs only gmail.compose. send_draft is separated out and is
# only ever called from the approval queue (Phase 4, gmail.send scope).
import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build

from .google_auth import credentials_for


def _svc(user_id: str):
    return build("gmail", "v1", credentials=credentials_for(user_id))


def list_unread(user_id: str, max_results: int = 10) -> list[dict]:
    """List the user's unread messages with sender, subject, and snippet."""
    svc = _svc(user_id)
    res = (
        svc.users()
        .messages()
        .list(userId="me", q="is:unread", maxResults=max_results)
        .execute()
    )
    out = []
    for m in res.get("messages", []):
        full = (
            svc.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata")
            .execute()
        )
        hdrs = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        out.append(
            {
                "id": m["id"],
                "thread_id": full["threadId"],
                "from": hdrs.get("From"),
                "subject": hdrs.get("Subject"),
                "snippet": full.get("snippet"),
            }
        )
    return out


def _extract_plain(payload: dict) -> str:
    """Best-effort extraction of the text/plain body from a message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _extract_plain(part)
        if text:
            return text
    # Fall back to whatever body data is present.
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def get_thread_text(user_id: str, thread_id: str) -> str:
    """Concatenate the plain-text bodies of every message in a thread."""
    svc = _svc(user_id)
    t = (
        svc.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    parts = []
    for msg in t["messages"]:
        body = _extract_plain(msg["payload"])
        parts.append(body)
    return "\n\n---\n\n".join(parts)


def thread_participants(user_id: str, thread_id: str) -> set[str]:
    """Email addresses that actually appear in a thread (From/To/Cc).

    Used by the recipient-confirmation guardrail so an injected address that
    never appeared in the conversation cannot become a recipient.
    """
    svc = _svc(user_id)
    t = (
        svc.users()
        .threads()
        .get(userId="me", id=thread_id, format="metadata")
        .execute()
    )
    addrs: set[str] = set()
    for msg in t["messages"]:
        for h in msg["payload"]["headers"]:
            if h["name"] in ("From", "To", "Cc"):
                for token in h["value"].split(","):
                    addrs.add(_bare_address(token))
    return {a for a in addrs if a}


def _bare_address(raw: str) -> str:
    """Pull `foo@bar.com` out of `Name <foo@bar.com>`."""
    raw = raw.strip()
    if "<" in raw and ">" in raw:
        return raw[raw.index("<") + 1 : raw.index(">")].strip().lower()
    return raw.lower()


def create_draft(
    user_id: str, to: str, subject: str, body: str, thread_id: str | None = None
) -> dict:
    """Needs ONLY gmail.compose. Creates a draft; does NOT send."""
    svc = _svc(user_id)
    mime = MIMEText(body)
    mime["To"], mime["Subject"] = to, subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    msg = {"message": {"raw": raw}}
    if thread_id:
        msg["message"]["threadId"] = thread_id
    return svc.users().drafts().create(userId="me", body=msg).execute()


def update_draft(
    user_id: str,
    draft_id: str,
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> dict:
    """Overwrite an existing draft's content. Needs only gmail.compose.

    Called from the approval queue when the user edits a draft before approving,
    so the edited text is what actually gets sent.
    """
    svc = _svc(user_id)
    mime = MIMEText(body)
    mime["To"], mime["Subject"] = to, subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    msg = {"message": {"raw": raw}}
    if thread_id:
        msg["message"]["threadId"] = thread_id
    return svc.users().drafts().update(userId="me", id=draft_id, body=msg).execute()


def send_draft(user_id: str, draft_id: str) -> dict:
    """PHASE 4 ONLY — requires gmail.send. Called solely from the approval queue."""
    return (
        _svc(user_id)
        .users()
        .drafts()
        .send(userId="me", body={"id": draft_id})
        .execute()
    )
