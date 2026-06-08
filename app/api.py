# app/api.py — FastAPI: chat + approval + OAuth endpoints.
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import google_auth, queue
from .agent import run_turn
from .config import settings
from .style import retrieve_style

app = FastAPI(title="Atlas Inbox")


class ChatRequest(BaseModel):
    user_id: str
    message: str


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- OAuth (connect a sandbox Google account once) ----------------------------

@app.get("/oauth/start")
def oauth_start():
    auth_url, _state = google_auth.build_auth_url()
    return RedirectResponse(auth_url)


@app.get("/oauth/callback")
def oauth_callback(code: str, state: str | None = None):
    google_auth.exchange_code(code, state=state, user_id=settings().default_user_id)
    return {"status": "connected", "user_id": settings().default_user_id}


# --- Chat (agent proposes into the queue) -------------------------------------

@app.post("/v1/chat")
async def chat(req: ChatRequest):
    style = retrieve_style(req.message)  # ground drafts in your voice
    reply = await run_turn(req.user_id, req.message, style)  # proposes into queue
    return {"reply": reply, "pending": queue.list_pending()}


# --- Approval queue -----------------------------------------------------------

@app.get("/v1/queue")
def get_queue():
    return queue.list_pending()


@app.post("/v1/queue/{action_id}/approve")
async def approve(action_id: str, req: Request):
    user_id = req.headers.get("x-user-id", settings().default_user_id)
    try:
        edits = await req.json()
    except Exception:
        edits = {}
    if not isinstance(edits, dict):
        edits = {}
    return queue.approve(user_id, action_id, edits=edits)


@app.post("/v1/queue/{action_id}/reject")
def reject(action_id: str):
    return queue.reject(action_id)
