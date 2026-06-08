# 📬 Atlas Inbox

**A Gmail + Calendar productivity copilot that drafts replies in your voice and proposes meeting times — but never sends or schedules anything without your explicit approval.**

Atlas Inbox reads your unread mail and your day, drafts replies grounded in your past writing style (RAG), proposes conflict-free meeting slots, and routes every consequential action through a single **human-in-the-loop approval queue**. It is built to exercise every production GenAI concept — RAG, an agent loop, gated tool use, prompt-injection defense, evals in CI, and live deployment — at a scope one engineer can finish.

> **The honest edge:** most portfolio agents can write an email. This one foregrounds what most skip — the **draft-never-send** boundary, **prompt-injection defense**, **evaluation with published numbers**, **tracing**, and a **live deployment**.

---

## Architecture

```
                   ┌──────────────────────────────────────────┐
 you ──HTTPS──────▶│  RENDER WEB SERVICE (FastAPI + Agents SDK) │ ◀── git push = deploy
                   │  chat · agent loop · guardrails ·          │     autoscale · streaming
                   │  APPROVAL QUEUE (review / edit / approve)   │
                   └──┬──────────┬──────────┬───────────┬───────┘
                      ▼          ▼          ▼           ▼
               ┌───────────┐ ┌────────┐ ┌──────────┐ ┌────────────┐
               │ OpenAI API│ │ Gmail/ │ │ Render    │ │ Langfuse   │
               │ LLM+embed │ │Calendar│ │ Postgres  │ │ Cloud      │
               │[external] │ │ APIs   │ │+pgvector  │ │traces/evals│
               └───────────┘ └────────┘ └────▲─────┘ └────────────┘
                                              │
 BACKGROUND (Render Worker + Cron):           │
 • inbox sync: fetch new threads → store ──────┘
 • nightly: embed your sent mail → style corpus (pgvector)

 ── HARD RULE ───────────────────────────────────────────────────────────
 read_* and draft_* run freely.  send_email / create_event ONLY execute
 after explicit human approval in the queue.  Email bodies are UNTRUSTED.
 ─────────────────────────────────────────────────────────────────────────
```

Two flows: an **interactive path** (you chat; the agent reads, reasons, and produces drafts/proposals into the queue) and a **background path** (a worker syncs the inbox; a cron refreshes your style corpus). The approval queue is the gate between the agent's proposals and any real-world action.

## Safety design

The dangerous combination — the **lethal trifecta** — is one agent with private-data access **+** untrusted-content exposure **+** the ability to act externally. Atlas Inbox reads private mail and untrusted content, but **acting is severed from the agent**: it can only *propose*, and a human *executes*. That structural separation — not the model's good behaviour — is the real defense.

The layered defense, in order:

1. **Untrusted-data delimiting** — every email body the agent reads is wrapped (`wrap_untrusted`) so injected instructions are visibly data, not commands.
2. **Input tripwire** — directive-like patterns (`ignore previous`, `change the recipient`, …) halt the run for review.
3. **No-fabrication output guard** — an LLM-as-judge blocks drafts that invent commitments, dates, or names the user never gave.
4. **Recipient confirmation** — a send can only go to a real thread participant or a user-confirmed address, so an injected "send to attacker@evil.com" cannot redirect mail.
5. **The approval gate** — the ultimate backstop: nothing leaves without you clicking **Approve**.

**Least-privilege OAuth:** v1 requests only `gmail.readonly`, `gmail.compose` (drafts, *not* send), and `calendar.readonly`. The `gmail.send` / `calendar.events` scopes are added last, behind the gate — so for most of the build the app literally *cannot* send. OAuth tokens are **Fernet-encrypted at rest** and never logged.

## Repo layout

```
atlas-inbox/
  app/
    config.py         # typed settings
    db.py             # psycopg connection pool + pgvector registration
    google_auth.py    # OAuth flow + encrypted token storage
    gmail.py          # read threads, create drafts, (gated) send
    calendar.py       # read events, free/busy, (gated) create event
    style.py          # embed past sent mail; retrieve style examples (RAG)
    tools.py          # the agent's tools (read + propose; gated actions)
    guardrails.py     # injection defense, recipient confirm, no-fabrication
    agent.py          # the OpenAI Agents SDK agent
    queue.py          # approval queue: list / approve / reject / execute
    api.py            # FastAPI: chat + approval + OAuth endpoints
    observability.py  # Langfuse wiring
  worker/sync.py      # inbox sync + nightly style-corpus refresh
  ui/app.py           # Streamlit approval queue + chat
  eval/               # run.py, judge.py, golden.jsonl
  sql/schema.sql      # the Postgres schema (Section 5)
  tests/
  render.yaml         # web + worker + cron + managed Postgres
  Dockerfile
  .github/workflows/ci.yml
```

## Quick start (local)

```bash
# 1. Google Cloud: enable Gmail + Calendar APIs, make an OAuth client (web),
#    add your SANDBOX account as a test user. Note client id/secret + redirect URI.

# 2. Install + configure
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in OPENAI_API_KEY, GOOGLE_*, TOKEN_ENC_KEY, DATABASE_URL
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # TOKEN_ENC_KEY

# 3. Database
psql "$DATABASE_URL" -f sql/schema.sql   # CREATE EXTENSION vector + tables

# 4. Run the API, connect your sandbox account, then run the UI
uvicorn app.api:app --reload
#    visit http://localhost:8000/oauth/start once to grant consent
streamlit run ui/app.py
```

Use a **sandbox Google account**, never your real one.

## Tests & the eval gate

```bash
pytest tests/                                            # hermetic unit tests (no network)
python -m eval.run --dataset eval/golden.jsonl --min-pass 0.85   # the CI eval gate
```

The golden set measures three things that fail differently: **draft quality** (LLM-as-judge), **scheduling correctness** (deterministic — slot must conflict with nothing), and **adversarial** cases (injection attempts that must be refused). A pass-rate below the threshold fails CI and blocks the Render deploy.

## Deploy (Render)

Everything is declared in [`render.yaml`](render.yaml): the web service, the background worker, the nightly cron, and the managed Postgres.

1. Create a Google Cloud project; enable Gmail + Calendar APIs; configure the OAuth consent screen (External/Testing); add your sandbox account as a test user. Create a web OAuth client.
2. Push the repo to GitHub.
3. In Render: **New → Blueprint**, point at the repo. Render reads `render.yaml` and provisions the DB, web service, worker, and cron.
4. Set secrets in the `atlas-secrets` env group: OpenAI key, Google client id/secret, `TOKEN_ENC_KEY`, Langfuse keys.
5. Run the one-time DB setup (`sql/schema.sql`).
6. Visit the service URL, complete OAuth consent once, and you're live.

## Build path (six phases)

| Phase | What ships | Teaches |
|------|-----------|---------|
| 1 | Read & summarize: repo, FastAPI, Postgres+pgvector, read-only OAuth, `read_inbox`/`read_calendar` | tool use, structured output, deployment, OAuth |
| 2 | Style-grounded drafting: nightly style refresh + `retrieve_style` RAG + `draft_reply` (compose only) | RAG, generation, the draft-not-send boundary |
| 3 | Scheduling: free/busy + `find_meeting_slots` + `propose_event` | the agent loop, planning, objective correctness |
| 4 | Gated actions + guardrails: add send/events scopes + the queue execute path + injection/fabrication/recipient guards | gated actions, HITL, safety |
| 5 | Evals + observability: golden set, CI eval gate, Langfuse tracing | evaluation, observability, the feedback loop |
| 6 | Polish & package: Streamlit UI, README, demo video, write-up | portfolio packaging |

---

> ⚠️ This is a teaching scaffold adapted from a capstone spec — realistic and close to runnable, but verify current Google OAuth scopes, OpenAI model names, and Render/Langfuse specifics before relying on it, and always develop against a sandbox account.
