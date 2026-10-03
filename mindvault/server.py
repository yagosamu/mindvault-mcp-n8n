"""MCP server over the MindVault vault.

Exposes the vault twice, because MCP's primitives answer different questions:

* **Tools** are model-controlled — the model decides to call them mid-reasoning.
  Searching is a decision ("I should look this up"), so it is a tool.
* **Resources** are application-controlled — the host or the person picks them,
  like opening a file. A note has a stable address and no side effects, so it is
  a resource (`note://<path>`), not a tool.

Serving the same data through both is deliberate: the model can search its way to
a note, and a human can attach one directly without the model guessing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from mindvault import store
from mindvault.config import Settings, get_settings


@dataclass
class VaultContext:
    """Built once at startup and shared by every handler."""

    client: httpx.AsyncClient
    settings: Settings


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[VaultContext]:
    """One HTTP client for the server's lifetime.

    Opening a connection pool per call would pay the TLS handshake to OpenAI on
    every single search. The lifespan runs once, not per request.
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.request_timeout_s) as client:
        yield VaultContext(client=client, settings=settings)


mcp = MCPServer("MindVault", lifespan=lifespan)


def _ctx(ctx: Context[VaultContext]) -> VaultContext:
    return ctx.request_context.lifespan_context


def _format(note: store.Note) -> str:
    head = f"# {note.title}\n_{note.path}_"
    if note.score is not None:
        head += f" · similarity {note.score:.3f}"
    return f"{head}\n\n{note.text}"


@mcp.tool()
async def search_notes(
    query: str,
    ctx: Context[VaultContext],
    limit: int = 5,
    min_score: float = 0.3,
) -> str:
    """Search the vault by meaning and return the matching notes.

    Use this to answer questions about past meetings, decisions, action items or
    anything else captured in the vault. The query is matched semantically, so
    phrase it as the idea you are looking for rather than as keywords.

    Args:
        query: What to look for, in natural language.
        limit: How many notes to return at most.
        min_score: Cosine similarity floor, 0 to 1. Raise it to cut weak matches;
            lower it when a search that should have hit comes back empty.

    Returns:
        The matching notes with their vault paths and similarity scores, or a
        plain statement that nothing cleared the threshold.
    """
    vault = _ctx(ctx)
    try:
        vector = await store.embed(vault.client, vault.settings, query)
        notes = await store.search(vault.client, vault.settings, vector, limit, min_score)
    except store.VaultError as exc:
        raise ToolError(str(exc)) from exc

    if not notes:
        # Saying so explicitly beats returning nothing: it tells the model the
        # vault was consulted and came up empty, which is itself an answer.
        return (
            f"No note in the vault scored above {min_score} for {query!r}. "
            "Either it was never captured, or the threshold is too strict."
        )
    return "\n\n---\n\n".join(_format(n) for n in notes)


@mcp.tool()
async def get_note(path: str, ctx: Context[VaultContext]) -> str:
    """Read one note in full, given its exact vault path.

    Paths look like '01 - Projetos/2026-10-03 - kickoff.md' and are returned by
    search_notes and list_notes. Prefer search_notes when you do not already
    know the path.

    Args:
        path: The note's path inside the vault, exactly as listed.

    Returns:
        The note's distilled content. The raw transcript that some notes keep in
        a collapsed callout is not indexed and therefore not returned.
    """
    vault = _ctx(ctx)
    try:
        note = await store.get_by_path(vault.client, vault.settings, path)
    except store.VaultError as exc:
        raise ToolError(str(exc)) from exc

    if note is None:
        raise ToolError(f"No indexed note at {path!r}. Use list_notes to see what exists.")
    return _format(note)


@mcp.tool()
async def list_notes(
    ctx: Context[VaultContext],
    folder: str | None = None,
    limit: int = 100,
) -> str:
    """List the notes in the vault, optionally within one PARA folder.

    The vault is organised with PARA: '00 - Caixa de Entrada' (unsorted),
    '01 - Projetos' (has an end date), '02 - Áreas' (ongoing responsibility),
    '03 - Recursos' (reference), '04 - Arquivos' (inactive).

    Args:
        folder: Restrict to one PARA folder, written exactly as above.
        limit: Maximum number of notes to scan.

    Returns:
        One line per note: its path and title.
    """
    vault = _ctx(ctx)
    try:
        notes = await store.list_paths(vault.client, vault.settings, folder, limit)
    except store.VaultError as exc:
        raise ToolError(str(exc)) from exc

    if not notes:
        where = f" in {folder!r}" if folder else ""
        return f"The vault has no indexed notes{where}."
    return "\n".join(f"- {n.path} — {n.title}" for n in notes)


# {+path} is RFC 6570 *reserved* expansion: unlike {path}, it matches a value
# containing "/", which every vault path has ("01 - Projetos/nota.md").
@mcp.resource("note://{+path}")
async def note_resource(path: str, ctx: Context[VaultContext]) -> str:
    """A single vault note, addressable by its path."""
    vault = _ctx(ctx)
    note = await store.get_by_path(vault.client, vault.settings, path)
    if note is None:
        raise ValueError(f"No indexed note at {path!r}")
    return _format(note)


def main() -> None:
    """Entry point: speak MCP over stdio, which is how a local host launches us."""
    mcp.run()


if __name__ == "__main__":
    main()
