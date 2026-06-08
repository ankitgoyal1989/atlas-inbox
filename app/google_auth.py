# app/google_auth.py — OAuth flow + encrypted token storage.
#
# Least-privilege scopes first. gmail.send / calendar.events are intentionally
# commented out until Phase 4, so for most of the build the app *cannot* send.
import json

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .config import settings
from .db import pg

# OAuth scopes — smallest set first.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",   # read mail/threads
    "https://www.googleapis.com/auth/gmail.compose",    # create drafts (NOT send)
    "https://www.googleapis.com/auth/calendar.readonly",  # read calendar / free-busy
    # Phase 4 — the gated action powers. These only ever execute from the
    # approval queue, after explicit human approval.
    "https://www.googleapis.com/auth/gmail.send",       # send approved drafts
    "https://www.googleapis.com/auth/calendar.events",  # create approved events
]


def _fernet() -> Fernet:
    """Fernet cipher built from the env key. Tokens are encrypted at rest."""
    key = settings().token_enc_key
    if not key:
        raise RuntimeError("TOKEN_ENC_KEY is not set; cannot encrypt OAuth tokens")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _client_config() -> dict:
    s = settings()
    return {
        "web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [s.google_redirect_uri],
        }
    }


# --- OAuth flow ---------------------------------------------------------------

# Google's OAuth flow uses PKCE: authorization_url() generates a one-time
# code_verifier that must be replayed at token exchange. Since the auth-URL and
# callback are separate requests (and separate Flow instances), we stash the
# verifier here keyed by `state`. In-memory is fine for single-user local/dev;
# for multi-instance prod, persist this in the DB or a signed cookie instead.
_PENDING_VERIFIERS: dict[str, str] = {}


def build_auth_url(state: str | None = None) -> tuple[str, str]:
    """Start the consent flow. Returns (authorization_url, state)."""
    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=settings().google_redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",  # we want a refresh token
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    # Carry the PKCE verifier across to the callback.
    if flow.code_verifier:
        _PENDING_VERIFIERS[state] = flow.code_verifier
    return auth_url, state


def exchange_code(
    code: str, state: str | None = None, user_id: str | None = None
) -> None:
    """Exchange an auth code for tokens and persist them (encrypted)."""
    user_id = user_id or settings().default_user_id
    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=settings().google_redirect_uri
    )
    # Replay the PKCE verifier captured during build_auth_url().
    flow.code_verifier = _PENDING_VERIFIERS.pop(state, None)
    flow.fetch_token(code=code)
    _store_credentials(user_id, flow.credentials)


# --- Token persistence --------------------------------------------------------

def _store_credentials(user_id: str, creds: Credentials) -> None:
    """Encrypt and upsert the credentials JSON for a user. Never logged."""
    payload = json.dumps(
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
    )
    token_enc = _fernet().encrypt(payload.encode())
    with pg() as cur:
        cur.execute(
            """
            INSERT INTO google_tokens (user_id, token_enc, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (user_id)
            DO UPDATE SET token_enc = EXCLUDED.token_enc, updated_at = now()
            """,
            (user_id, token_enc),
        )


def _load_credentials(user_id: str) -> Credentials:
    with pg() as cur:
        cur.execute(
            "SELECT token_enc FROM google_tokens WHERE user_id = %s", (user_id,)
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"No stored Google credentials for user {user_id!r}")
    data = json.loads(_fernet().decrypt(bytes(row[0])).decode())
    return Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes"),
    )


def credentials_for(user_id: str | None = None) -> Credentials:
    """Return valid credentials for a user, refreshing + re-persisting if needed."""
    user_id = user_id or settings().default_user_id
    creds = _load_credentials(user_id)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        # IMPORTANT: persist the refreshed token, or we'd refresh on every call.
        _store_credentials(user_id, creds)
    return creds
