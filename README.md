# 📬 Atlas Inbox

**A Gmail + Calendar productivity copilot that drafts replies in your voice and proposes meeting times — and never sends or schedules anything without your explicit approval.**

[![CI](https://github.com/ankitgoyal1989/atlas-inbox/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ankitgoyal1989/atlas-inbox/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Atlas Inbox reads your unread mail and your schedule, drafts replies grounded in your past writing style (RAG), proposes conflict-free meeting slots, and routes every consequential action through a single **human-in-the-loop approval queue**. It is a production-shaped agent: RAG, an agent loop with tool use, gated actions, layered prompt-injection defense, evaluations wired into CI, full request tracing, and a live deployment.

> **What sets it apart:** most email agents stop at "the model writes a draft." Atlas Inbox is built around the parts that actually matter in production — a **draft-never-send** boundary, **prompt-injection defense**, **evaluation with measurable pass-rates**, **observability**, and a **live deployment**.

---

## Demo

- **Live app:** _add your Render UI URL here_ (`https://atlas-inbox-ui.onrender.com`)
- **API:** _add your Render API URL here_ (`https://atlas-inbox-api.onrender.com`)
- **Walkthrough video:** _add a 2–3 min Loom link_

| | |
|---|---|
| Chat + approval queue | <img width="687" height="501" alt="Screenshot 2026-06-08 at 4 27 44 PM" src="https://github.com/user-attachments/assets/e04d22e6-4492-4715-9be8-3cd65e58702e" />  <img width="504" height="501" alt="Screenshot 2026-06-08 at 4 32 11 PM" src="https://github.com/user-attachments/assets/697c587e-b74a-4215-94a5-90319654700c" /> |
| An injection attempt being refused | <img width="720" height="328" alt="Screenshot 2026-06-08 at 4 28 39 PM" src="https://github.com/user-attachments/assets/d4b93248-799c-4a9c-a91c-14eab84c474b" /> |
| A Langfuse trace of one agent run | <img width="1268" height="646" alt="Screenshot 2026-06-08 at 4 28 56 PM" src="https://github.com/user-attachments/assets/b3199155-c0f3-4225-bcba-1e4ad718a709" /> |

## Features

- **Reads & summarizes** unread mail and the day's calendar on demand.
- **Drafts replies in your voice** — retrieves stylistically similar past sent emails (pgvector) and grounds the draft in the thread.
- **Composes new emails** from a plain instruction.
- **Schedules meetings** — reads free/busy, finds conflict-free slots in your working hours, and proposes events. Fully **timezone-aware**.
- **Approval queue** — review, **edit**, and approve/reject every proposed send or event before it happens.
- **Layered safety** — prompt-injection tripwire, recipient confirmation, no-fabrication guard, and the approval gate.
- **Observability & evals** — every run is traced to Langfuse; a golden-set evaluation gate blocks regressions in CI.

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
5. **The approval gate** — the ultimate backstop: nothing leaves without a human clicking **Approve**.

**Least-privilege OAuth:** the `gmail.send` and `calendar.events` powers are exercised **only** from inside the approval queue, after explicit human approval — the agent itself never calls them. OAuth tokens are **Fernet-encrypted at rest** and never logged.

## Tech stack

| Layer | Choice |
|------|--------|
| API + agent host | FastAPI on Render |
| Agent orchestration | OpenAI Agents SDK (agent loop, tools, guardrails) |
| LLM + embeddings | OpenAI API |
| State / vectors | Render Postgres + pgvector |
| Background jobs | Render Worker (inbox sync) + Cron (nightly style refresh) |
| Integrations | Gmail API + Google Calendar API |
| Observability | Langfuse (OpenTelemetry + OpenInference) |
| Evals / CI | golden-set eval gate + GitHub Actions |
| UI | Streamlit |

## Repo layout

```
atlas-inbox/
  app/
    config.py         # typed settings + timezone helper
    db.py             # psycopg connection pool + pgvector registration
    google_auth.py    # OAuth (PKCE) flow + encrypted token storage
    gmail.py          # read threads, create/update drafts, (gated) send
    calendar.py       # read events, free/busy, find slots, (gated) create event
    style.py          # embed past sent mail; retrieve style examples (RAG)
    tools.py          # the agent's tools (read + propose; gated actions elsewhere)
    guardrails.py     # injection defense, recipient confirm, no-fabrication
    agent.py          # the OpenAI Agents SDK agent + run loop
    queue.py          # approval queue: list / approve / reject / execute
    api.py            # FastAPI: chat + approval + OAuth endpoints
    observability.py  # Langfuse / OpenTelemetry wiring
  worker/sync.py      # inbox sync + nightly style-corpus refresh
  ui/app.py           # Streamlit approval queue + chat
  eval/               # run.py, judge.py, golden.jsonl
  sql/schema.sql      # the Postgres schema
  tests/              # hermetic unit tests
  render.yaml         # web + UI + worker + cron + managed Postgres
  Dockerfile
  .github/workflows/ci.yml
```

## Quick start (local)

```bash
# 1. Google Cloud: enable Gmail + Calendar APIs, create a web OAuth client,
#    and add your account as a test user (use a sandbox account).

# 2. Install + configure
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # fill in OPENAI_API_KEY, GOOGLE_*, TOKEN_ENC_KEY, DATABASE_URL
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # TOKEN_ENC_KEY

# 3. Database (Postgres 16 + pgvector)
psql "$DATABASE_URL" -f sql/schema.sql

# 4. Run the API, connect Google once, then run the UI
uvicorn app.api:app --reload
#    open http://localhost:8000/oauth/start to grant consent
streamlit run ui/app.py        # http://localhost:8501
```

Set `ATLAS_TIMEZONE` (e.g. `Asia/Kolkata`) so "tomorrow at 3pm" resolves in your local time.

## Tests & the evaluation gate

```bash
pytest tests/                                                    # hermetic unit tests (no network)
python -m eval.run --dataset eval/golden.jsonl --min-pass 0.85   # the CI eval gate
```

The golden set measures three things that fail differently: **draft quality** (LLM-as-judge), **scheduling correctness** (deterministic — a proposed slot must conflict with nothing), and **adversarial** cases (injection attempts that must be refused). A pass-rate below the threshold fails CI and blocks deploy.

## Deploy (Render)

The whole stack is declared in [`render.yaml`](render.yaml): the API, the Streamlit UI, the background worker, the nightly cron, the managed Postgres, and the `atlas-secrets` env group.

1. Push to GitHub.
2. In Render: **New → Blueprint**, point at the repo. Render provisions everything and prompts for the secrets in `atlas-secrets`.
3. Apply the schema once: `psql "$DATABASE_URL" -f sql/schema.sql`.
4. Add `https://<your-api>.onrender.com/oauth/callback` to the Google OAuth client's redirect URIs, and set `GOOGLE_REDIRECT_URI` to the same value.
5. Visit `/oauth/start` to connect, then open the UI URL. Live.

## Engineering notes

- **The agent cannot act on the world.** Tools only *read* or *propose*; the gated Google calls (`send_draft`, `create_event`) live exclusively in `queue.approve()`, reached only by a human clicking Approve.
- **Untrusted input is treated as data, not instructions** — the core defense against prompt injection in an email agent.
- **Errors degrade gracefully** — guardrail tripwires, turn limits, missing OAuth scopes, and Google API failures return actionable messages and leave proposed actions pending for retry, never a 500.
- **Deterministic where it can be** — slot-finding is pure and unit-tested; only the subjective parts (draft quality) use an LLM judge.

## License

MIT — see [LICENSE](LICENSE).
