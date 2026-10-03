"""Settings for the MindVault MCP server.

Everything comes from the environment (or the project's .env), so the same code
runs on a laptop against the local stack and on a host where the values arrive
as real env vars. No secret is ever hard-coded or committed.

Note what is NOT here: GitHub. The indexer already copies each note's full text
into the Qdrant payload, so this server reads only from Qdrant and never needs a
repository token. One fewer secret in circulation is a design win, not an
accident.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration read from env vars, falling back to the stack's defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # the .env is shared with docker compose, which has more keys
    )

    # Reached from the HOST, so this is the published port (6353), not the
    # in-compose 6333 that n8n uses. Same store, different vantage point.
    qdrant_url: str = "http://localhost:6353"
    qdrant_api_key: str = ""
    qdrant_collection: str = "mindvault"

    openai_api_key: str = Field(
        default="",
        description="Required: queries must be embedded before they can be searched.",
    )
    # Must match whatever the indexer used. A query embedded with a different
    # model lands in a different space, and every distance becomes meaningless —
    # the search would not error, it would just quietly return nonsense.
    openai_embedding_model: str = "text-embedding-3-small"
    openai_base_url: str = "https://api.openai.com/v1"

    request_timeout_s: float = 30.0

    @property
    def search_url(self) -> str:
        return f"{self.qdrant_url.rstrip('/')}/collections/{self.qdrant_collection}/points/search"

    @property
    def scroll_url(self) -> str:
        return f"{self.qdrant_url.rstrip('/')}/collections/{self.qdrant_collection}/points/scroll"

    @property
    def embeddings_url(self) -> str:
        return f"{self.openai_base_url.rstrip('/')}/embeddings"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings are immutable for the process, so build them once."""
    return Settings()
