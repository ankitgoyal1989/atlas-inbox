# app/style.py — RAG that makes drafts sound like you.
#
# The "knowledge base" is your own past sent emails. Retrieving a few
# stylistically similar ones and showing them to the model is what makes drafts
# match your voice instead of sounding generic.
from openai import OpenAI

from .config import settings
from .db import pg

_oai: OpenAI | None = None


def _client() -> OpenAI:
    global _oai
    if _oai is None:
        _oai = OpenAI(api_key=settings().openai_api_key)
    return _oai


def embed(text: str) -> list[float]:
    return (
        _client()
        .embeddings.create(model=settings().embedding_model, input=text)
        .data[0]
        .embedding
    )


def index_sent_email(text: str) -> None:
    """Embed one past sent message and store it in the style corpus."""
    vec = embed(text)
    with pg() as cur:
        cur.execute(
            "INSERT INTO style_corpus (text, embedding) VALUES (%s, %s)",
            (text, vec),
        )


def retrieve_style(context: str, k: int = 3) -> list[str]:
    """Return the k most stylistically-similar past messages to `context`."""
    vec = embed(context)
    with pg() as cur:
        cur.execute(
            "SELECT text FROM style_corpus ORDER BY embedding <=> %s::vector LIMIT %s",
            (vec, k),
        )
        return [r[0] for r in cur.fetchall()]
