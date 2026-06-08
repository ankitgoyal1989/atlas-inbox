# ui/app.py — Streamlit approval queue + chat.
#
# A thin client over the FastAPI service. Talks only to /v1/chat and /v1/queue,
# so the safety boundary (draft-never-send, approval gate) lives entirely in the
# API — the UI cannot bypass it.
import os

import requests
import streamlit as st


def _normalize_base(raw: str) -> str:
    """Accept a full URL (local dev) or a bare host (Render fromService) and
    always return a scheme-qualified base with no trailing slash."""
    raw = raw.strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw  # Render gives a bare host; it serves HTTPS
    return raw


def _parse_response(r):
    """Safely parse an API response. Returns (ok, data) and never raises, so a
    500/HTML error body can't crash the UI."""
    try:
        data = r.json()
    except Exception:
        data = {"error": f"HTTP {r.status_code}: {r.text[:300] or 'no body'}"}
    ok = r.ok and not (isinstance(data, dict) and "error" in data)
    return ok, data


API_BASE = _normalize_base(os.environ.get("ATLAS_API_BASE", "http://localhost:8000"))
USER_ID = os.environ.get("ATLAS_DEFAULT_USER_ID", "me")

st.set_page_config(page_title="Atlas Inbox", page_icon="📬", layout="wide")
st.title("📬 Atlas Inbox")
st.caption("Draft-never-send · human-in-the-loop · injection-defended")

chat_col, queue_col = st.columns([3, 2])

# --- Chat ---------------------------------------------------------------------
with chat_col:
    st.subheader("Chat")
    if "history" not in st.session_state:
        st.session_state.history = []
    if "pending" not in st.session_state:
        st.session_state.pending = None

    # 1. Render the full conversation so far (includes the just-submitted question).
    for role, text in st.session_state.history:
        with st.chat_message(role):
            st.markdown(text)

    # 2. If there's an unanswered user message, show the thinking spinner *here*
    #    (above the input, right after the question) and call the API.
    if st.session_state.pending:
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    resp = requests.post(
                        f"{API_BASE}/v1/chat",
                        json={"user_id": USER_ID, "message": st.session_state.pending},
                        timeout=120,
                    )
                    resp.raise_for_status()
                    reply = resp.json().get("reply", "(no reply)")
                except Exception as exc:
                    reply = f"⚠️ Error: {exc}"
        st.session_state.history.append(("assistant", reply))
        st.session_state.pending = None
        st.rerun()

    # 3. The input box stays at the bottom. On submit, record the question and
    #    rerun immediately so it appears before we start "Thinking…".
    _placeholder = "e.g. 'Summarize my unread mail' or 'Draft a reply to the latest thread'"
    if prompt := st.chat_input(_placeholder):
        st.session_state.history.append(("user", prompt))
        st.session_state.pending = prompt
        st.rerun()

# --- Approval queue -----------------------------------------------------------
with queue_col:
    st.subheader("Approval queue")
    st.caption("Nothing is sent or created until you approve it here.")

    if st.button("🔄 Refresh"):
        st.rerun()

    try:
        pending = requests.get(f"{API_BASE}/v1/queue", timeout=30).json()
    except Exception as exc:
        st.error(f"Could not load queue: {exc}")
        pending = []

    if not pending:
        st.info("No pending actions.")

    for action in pending:
        kind = action["kind"]
        payload = action["payload"]
        with st.container(border=True):
            if kind == "send_email":
                st.markdown(f"**✉️ Send email** to `{payload.get('to')}`")
                st.markdown(f"*Subject:* {payload.get('subject')}")
                st.text_area(
                    "Body", payload.get("body", ""), key=f"body_{action['id']}",
                    height=140,
                )
            elif kind == "create_event":
                st.markdown(f"**📅 Create event** — {payload.get('summary')}")
                st.markdown(
                    f"*When:* {payload.get('start')} → {payload.get('end')}"
                )
                st.markdown(f"*Attendees:* {', '.join(payload.get('attendees', []))}")

            approve_col, reject_col = st.columns(2)
            if approve_col.button("✅ Approve", key=f"a_{action['id']}"):
                # Send any edits the user made in the body text box so the edited
                # version is what actually gets sent.
                edits = {}
                if kind == "send_email":
                    edits["body"] = st.session_state.get(
                        f"body_{action['id']}", payload.get("body", "")
                    )
                r = requests.post(
                    f"{API_BASE}/v1/queue/{action['id']}/approve",
                    headers={"x-user-id": USER_ID},
                    json=edits,
                    timeout=60,
                )
                ok, data = _parse_response(r)
                if ok:
                    st.success(data)
                    st.rerun()  # action done → refresh so the card disappears
                else:
                    st.error(data)  # leave the card so the user can retry
            if reject_col.button("❌ Reject", key=f"r_{action['id']}"):
                r = requests.post(
                    f"{API_BASE}/v1/queue/{action['id']}/reject", timeout=60
                )
                ok, data = _parse_response(r)
                if ok:
                    st.warning(data)
                    st.rerun()
                else:
                    st.error(data)
