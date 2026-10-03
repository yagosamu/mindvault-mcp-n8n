"""Reading the vault: embed a question, then search or fetch notes in Qdrant.

Both dependencies are plain HTTP, so this module talks to them with httpx
directly instead of pulling in the openai and qdrant-client SDKs. Two POSTs do
not justify two SDKs, and the tests end up asserting against the real wire
contract rather than against a mocked client object.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from mindvault.config import Settings


class VaultError(RuntimeError):
    """Raised when an upstream call fails in a way the caller should surface."""


@dataclass(frozen=True)
class Note:
    """One note from the vault. `score` is only set when it came from a search."""

    path: str
    title: str
    text: str
    tipo: str = ""
    score: float | None = None

    @property
    def folder(self) -> str:
        """The PARA bucket, derived from the path ('02 - Áreas/x.md' -> '02 - Áreas')."""
        return self.path.split("/", 1)[0] if "/" in self.path else ""

    @classmethod
    def from_point(cls, point: dict) -> "Note":
        payload = point.get("payload") or {}
        return cls(
            path=payload.get("path", ""),
            title=payload.get("titulo", ""),  # the indexer writes Portuguese keys
            text=payload.get("texto", ""),
            tipo=payload.get("tipo", ""),
            score=point.get("score"),
        )


async def embed(client: httpx.AsyncClient, settings: Settings, text: str) -> list[float]:
    """Turn a question into a vector using the SAME model the indexer used."""
    if not settings.openai_api_key:
        raise VaultError("OPENAI_API_KEY is not set; queries cannot be embedded.")

    response = await client.post(
        settings.embeddings_url,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={"model": settings.openai_embedding_model, "input": text},
    )
    if response.status_code != 200:
        raise VaultError(f"Embedding failed ({response.status_code}): {response.text[:200]}")
    return response.json()["data"][0]["embedding"]


def _qdrant_headers(settings: Settings) -> dict[str, str]:
    # Self-hosted Qdrant runs without auth; only send the header when set, since
    # an empty api-key is not the same as no api-key.
    return {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}


async def search(
    client: httpx.AsyncClient,
    settings: Settings,
    vector: list[float],
    limit: int = 5,
    min_score: float = 0.0,
    tipo: str | None = None,
) -> list[Note]:
    """Nearest notes to `vector`, dropping anything below `min_score`.

    The threshold is the point of this function. Without it a store always
    returns its `limit` closest rows, however far away they are — so a question
    the vault cannot answer still arrives at the model dressed as evidence.
    """
    body: dict = {"vector": vector, "limit": limit, "with_payload": True}
    if min_score > 0:
        body["score_threshold"] = min_score
    if tipo:
        # Filtering in Qdrant rather than after the fact: dropping rows here would
        # silently return fewer than `limit`, because the search already spent its
        # budget on the kinds the caller did not want.
        body["filter"] = {"must": [{"key": "tipo", "match": {"value": tipo}}]}

    response = await client.post(settings.search_url, headers=_qdrant_headers(settings), json=body)
    if response.status_code != 200:
        raise VaultError(f"Qdrant search failed ({response.status_code}): {response.text[:200]}")
    return [Note.from_point(p) for p in response.json().get("result", [])]


async def get_by_path(
    client: httpx.AsyncClient, settings: Settings, path: str
) -> Note | None:
    """Fetch one note by its exact vault path, or None if it is not indexed."""
    response = await client.post(
        settings.scroll_url,
        headers=_qdrant_headers(settings),
        json={
            "filter": {"must": [{"key": "path", "match": {"value": path}}]},
            "limit": 1,
            "with_payload": True,
            "with_vector": False,
        },
    )
    if response.status_code != 200:
        raise VaultError(f"Qdrant scroll failed ({response.status_code}): {response.text[:200]}")

    points = response.json().get("result", {}).get("points", [])
    return Note.from_point(points[0]) if points else None


async def list_paths(
    client: httpx.AsyncClient,
    settings: Settings,
    folder: str | None = None,
    limit: int = 100,
    tipo: str | None = None,
) -> list[Note]:
    """Every indexed note, optionally restricted to one PARA folder.

    Qdrant has no prefix filter, so the folder is applied here. The vault is
    small by nature — a few hundred notes — which keeps that honest.
    """
    response = await client.post(
        settings.scroll_url,
        headers=_qdrant_headers(settings),
        json={
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
            **({"filter": {"must": [{"key": "tipo", "match": {"value": tipo}}]}} if tipo else {}),
        },
    )
    if response.status_code != 200:
        raise VaultError(f"Qdrant scroll failed ({response.status_code}): {response.text[:200]}")

    notes = [Note.from_point(p) for p in response.json().get("result", {}).get("points", [])]
    if folder:
        notes = [n for n in notes if n.folder == folder]
    return sorted(notes, key=lambda n: n.path)
