"""Server tests driven through the in-memory MCP client.

These go through the real protocol path — schema validation, lifespan, context
injection — with only the two upstreams mocked. That is the layer where a wrong
tool signature or a broken lifespan actually shows up.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp import Client

from mindvault.server import mcp
from tests.conftest import EMBED_URL, FAKE_VECTOR, SCROLL_URL, SEARCH_URL, point

# anyio's plugin drives every async test in this module.
pytestmark = pytest.mark.anyio

KICKOFF = point(
    "01 - Projetos/2026-10-03 - kickoff.md",
    "Kickoff do Second Brain",
    "Decidimos self-host do n8n para nao depender de trial.",
    0.78,
)


def _embed_ok() -> None:
    respx.post(EMBED_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": FAKE_VECTOR}]})
    )


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


async def test_exposes_three_tools_and_one_resource_template(client: Client) -> None:
    names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"search_notes", "get_note", "list_notes"}

    templates = (await client.list_resource_templates()).resource_templates
    assert [t.uri_template for t in templates] == ["note://{+path}"]


@respx.mock
async def test_search_notes_returns_hits_with_source(client: Client) -> None:
    _embed_ok()
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": [KICKOFF]}))

    out = text_of(await client.call_tool("search_notes", {"query": "por que self-host?"}))

    assert "Kickoff do Second Brain" in out
    # Citing the path is the whole point: an answer the person cannot trace back
    # to a note is indistinguishable from one the model invented.
    assert "01 - Projetos/2026-10-03 - kickoff.md" in out
    assert "0.780" in out


@respx.mock
async def test_search_notes_says_so_when_nothing_clears_the_threshold(client: Client) -> None:
    _embed_ok()
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))

    out = text_of(
        await client.call_tool("search_notes", {"query": "capital da Mongolia", "min_score": 0.5})
    )

    # An explicit "nothing matched" tells the model the vault was consulted.
    # Returning an empty string would read as a failed call instead.
    assert "No note in the vault scored above 0.5" in out


@respx.mock
async def test_search_notes_passes_caller_overrides_through(client: Client) -> None:
    _embed_ok()
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))

    await client.call_tool("search_notes", {"query": "x", "limit": 2, "min_score": 0.9})

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["limit"] == 2
    assert sent["score_threshold"] == 0.9


@respx.mock
async def test_get_note_returns_the_note(client: Client) -> None:
    respx.post(SCROLL_URL).mock(
        return_value=httpx.Response(200, json={"result": {"points": [KICKOFF]}})
    )
    out = text_of(
        await client.call_tool("get_note", {"path": "01 - Projetos/2026-10-03 - kickoff.md"})
    )
    assert "self-host do n8n" in out


@respx.mock
async def test_get_note_errors_on_unknown_path(client: Client) -> None:
    respx.post(SCROLL_URL).mock(
        return_value=httpx.Response(200, json={"result": {"points": []}})
    )
    result = await client.call_tool("get_note", {"path": "99 - Nope/x.md"})

    # A tool failure is a *result*, not a transport error: it reaches the model
    # as readable content so it can correct course, which is the point.
    assert result.is_error is True
    assert "No indexed note" in text_of(result)


@respx.mock
async def test_list_notes_filters_by_para_folder(client: Client) -> None:
    respx.post(SCROLL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "points": [
                        KICKOFF,
                        point("02 - Áreas/financas.md", "Financas", "x"),
                    ]
                }
            },
        )
    )
    out = text_of(await client.call_tool("list_notes", {"folder": "02 - Áreas"}))

    assert "02 - Áreas/financas.md" in out
    assert "kickoff" not in out


@respx.mock
async def test_note_resource_serves_the_same_note_by_uri(client: Client) -> None:
    respx.post(SCROLL_URL).mock(
        return_value=httpx.Response(200, json={"result": {"points": [KICKOFF]}})
    )
    result = await client.read_resource("note://01 - Projetos/2026-10-03 - kickoff.md")
    assert "Kickoff do Second Brain" in result.contents[0].text


@respx.mock
async def test_embedding_failure_surfaces_as_a_tool_error(client: Client) -> None:
    respx.post(EMBED_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    result = await client.call_tool("search_notes", {"query": "x"})

    assert result.is_error is True
    assert "429" in text_of(result)


@respx.mock
async def test_search_notes_passes_tipo_to_qdrant(client: Client) -> None:
    _embed_ok()
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))

    await client.call_tool("search_notes", {"query": "x", "tipo": "referencia"})

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["filter"]["must"][0]["match"]["value"] == "referencia"


@respx.mock
async def test_empty_result_names_the_restriction(client: Client) -> None:
    _embed_ok()
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))

    out = text_of(await client.call_tool("search_notes", {"query": "x", "tipo": "clip"}))

    # Saying which slice was searched stops the model from concluding the vault
    # is empty when only one kind of note was consulted.
    assert "type 'clip'" in out


@respx.mock
async def test_hits_show_the_kind_of_note(client: Client) -> None:
    _embed_ok()
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": [KICKOFF]}))

    out = text_of(await client.call_tool("search_notes", {"query": "x"}))
    assert "· referencia" in out
