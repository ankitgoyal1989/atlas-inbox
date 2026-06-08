# app/observability.py — Langfuse wiring for the OpenAI Agents SDK.
#
# Langfuse v3/v4 is itself an OpenTelemetry SDK: get_client() configures the
# global tracer provider with Langfuse's exporter. We then instrument the Agents
# SDK with OpenInference (no explicit provider, so it uses that same global
# provider) and its spans flow to Langfuse.
#
# To populate TRACE-level input/output (the OpenInference "Agent workflow" span
# only annotates child observations), run_turn wraps each turn in a root span we
# annotate via get_lf_client(). If keys are absent this is a no-op.
import os

from .config import settings

_initialized = False
_client = None


def init_observability() -> bool:
    """Configure Langfuse tracing. Returns True if tracing is active."""
    global _initialized, _client
    if _initialized:
        return True

    s = settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return False

    # The Langfuse SDK reads these from the environment.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", s.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", s.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", s.langfuse_host)

    try:
        from langfuse import get_client
        from openinference.instrumentation.openai_agents import (
            OpenAIAgentsInstrumentor,
        )

        client = get_client()  # sets up the global OTel provider + exporter
        if not client.auth_check():
            print("[observability] Langfuse auth failed — check keys/host/region")
            return False

        # Instrument the Agents SDK into the same (global) provider.
        OpenAIAgentsInstrumentor().instrument()

        _client = client
        _initialized = True
        print(f"[observability] Langfuse tracing enabled → {s.langfuse_host}")
    except Exception as exc:  # never let observability wiring crash the app
        print(f"[observability] tracing disabled: {exc}")
        return False
    return _initialized


def get_lf_client():
    """Return the Langfuse client if tracing is active, else None."""
    return _client


def flush() -> None:
    """Force-export buffered spans. Call before a short-lived process exits."""
    if _client is not None:
        _client.flush()
