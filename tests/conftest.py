"""Shared fixtures.

The settings object is cached for the process, and it reads the project's real
.env — which holds a real OpenAI key. Tests must never depend on that, so every
test runs against values set here. Env vars outrank the .env file in
pydantic-settings, so setting them is enough; the cache just has to be dropped
so the next call rebuilds.
"""

from __future__ import annotations

import pytest

from mindvault.config import get_settings

QDRANT_URL = "http://qdrant.test:6333"
OPENAI_URL = "https://openai.test/v1"
COLLECTION = "mindvault"

SEARCH_URL = f"{QDRANT_URL}/collections/{COLLECTION}/points/search"
SCROLL_URL = f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll"
EMBED_URL = f"{OPENAI_URL}/embeddings"

# text-embedding-3-small is 1536-wide; the exact values are irrelevant to the
# wiring under test, only the width and that it round-trips untouched.
FAKE_VECTOR = [0.1] * 1536


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only.

    The MCP SDK builds on anyio task groups, and pytest-asyncio drives fixture
    setup and teardown from different tasks — which trips anyio's cancel-scope
    check. anyio's own plugin keeps them in one task.
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def test_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QDRANT_URL", QDRANT_URL)
    monkeypatch.setenv("QDRANT_API_KEY", "")
    monkeypatch.setenv("QDRANT_COLLECTION", COLLECTION)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", OPENAI_URL)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def point(
    path: str, titulo: str, texto: str, score: float | None = None, tipo: str = "referencia"
) -> dict:
    """A Qdrant point shaped the way the n8n indexer writes it."""
    p: dict = {
        "id": "abc",
        "payload": {"path": path, "titulo": titulo, "texto": texto, "tipo": tipo},
    }
    if score is not None:
        p["score"] = score
    return p
