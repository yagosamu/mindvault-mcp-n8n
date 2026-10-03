"""Unit tests for the vault store.

These assert the HTTP contract itself — URL, headers, request body — because
that contract is the thing that silently breaks when a dependency changes. A
mocked SDK object would only prove we called our own wrapper.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from mindvault import store
from mindvault.config import Settings, get_settings
from tests.conftest import EMBED_URL, FAKE_VECTOR, SCROLL_URL, SEARCH_URL, point

# anyio's plugin drives every async test in this module.
pytestmark = pytest.mark.anyio


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@respx.mock
async def test_embed_sends_the_indexers_model(settings: Settings) -> None:
    route = respx.post(EMBED_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": FAKE_VECTOR}]})
    )
    async with httpx.AsyncClient() as client:
        vector = await store.embed(client, settings, "qual a rotina financeira?")

    assert vector == FAKE_VECTOR
    body = route.calls.last.request
    import json

    sent = json.loads(body.content)
    # Searching with a different model than the indexer used would not error —
    # it would just return quiet nonsense, so pin it.
    assert sent["model"] == "text-embedding-3-small"
    assert sent["input"] == "qual a rotina financeira?"
    assert body.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_embed_without_key_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    async with httpx.AsyncClient() as client:
        with pytest.raises(store.VaultError, match="OPENAI_API_KEY"):
            await store.embed(client, get_settings(), "x")


@respx.mock
async def test_embed_surfaces_upstream_error(settings: Settings) -> None:
    respx.post(EMBED_URL).mock(return_value=httpx.Response(401, text="bad key"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(store.VaultError, match="401"):
            await store.embed(client, settings, "x")


@respx.mock
async def test_search_applies_threshold_and_maps_payload(settings: Settings) -> None:
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"result": [point("02 - Áreas/fin.md", "Finanças", "conciliação", 0.83)]},
        )
    )
    async with httpx.AsyncClient() as client:
        notes = await store.search(client, settings, FAKE_VECTOR, limit=3, min_score=0.4)

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["score_threshold"] == 0.4
    assert sent["limit"] == 3

    [note] = notes
    # The indexer writes Portuguese payload keys; the mapping is the seam.
    assert note.path == "02 - Áreas/fin.md"
    assert note.title == "Finanças"
    assert note.text == "conciliação"
    assert note.score == 0.83
    assert note.folder == "02 - Áreas"


@respx.mock
async def test_search_omits_threshold_when_zero(settings: Settings) -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))
    async with httpx.AsyncClient() as client:
        await store.search(client, settings, FAKE_VECTOR, min_score=0.0)

    import json

    # Sending score_threshold=0 is not the same as sending nothing: it is a
    # filter that happens to pass everything, and it still costs a comparison.
    assert "score_threshold" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_get_by_path_filters_and_returns_none_when_absent(settings: Settings) -> None:
    route = respx.post(SCROLL_URL).mock(
        return_value=httpx.Response(200, json={"result": {"points": []}})
    )
    async with httpx.AsyncClient() as client:
        assert await store.get_by_path(client, settings, "01 - Projetos/x.md") is None

    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["filter"]["must"][0]["match"]["value"] == "01 - Projetos/x.md"
    assert sent["with_vector"] is False  # the caller wants text, not 1536 floats


@respx.mock
async def test_list_paths_filters_by_folder_and_sorts(settings: Settings) -> None:
    respx.post(SCROLL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "points": [
                        point("02 - Áreas/b.md", "B", ""),
                        point("01 - Projetos/a.md", "A", ""),
                        point("02 - Áreas/a.md", "A2", ""),
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as client:
        todas = await store.list_paths(client, settings)
        areas = await store.list_paths(client, settings, folder="02 - Áreas")

    assert [n.path for n in todas] == [
        "01 - Projetos/a.md",
        "02 - Áreas/a.md",
        "02 - Áreas/b.md",
    ]
    assert [n.path for n in areas] == ["02 - Áreas/a.md", "02 - Áreas/b.md"]


@respx.mock
async def test_qdrant_api_key_header_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))
    async with httpx.AsyncClient() as client:
        await store.search(client, get_settings(), FAKE_VECTOR)
    assert "api-key" not in respx.calls.last.request.headers

    monkeypatch.setenv("QDRANT_API_KEY", "secret")
    get_settings.cache_clear()
    async with httpx.AsyncClient() as client:
        await store.search(client, get_settings(), FAKE_VECTOR)
    assert respx.calls.last.request.headers["api-key"] == "secret"


@respx.mock
async def test_search_filters_by_tipo_server_side(settings: Settings) -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))
    async with httpx.AsyncClient() as client:
        await store.search(client, settings, FAKE_VECTOR, tipo="referencia")

    import json

    sent = json.loads(route.calls.last.request.content)
    # Filtering must reach Qdrant, not happen afterwards: dropping rows locally
    # would return fewer than `limit`, since the search already spent its budget.
    assert sent["filter"] == {"must": [{"key": "tipo", "match": {"value": "referencia"}}]}


@respx.mock
async def test_search_omits_filter_when_tipo_is_none(settings: Settings) -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"result": []}))
    async with httpx.AsyncClient() as client:
        await store.search(client, settings, FAKE_VECTOR)

    import json

    assert "filter" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_note_carries_tipo_from_payload(settings: Settings) -> None:
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"result": [point("03 - Recursos/x.md", "X", "y", 0.5, tipo="clip")]}
        )
    )
    async with httpx.AsyncClient() as client:
        [note] = await store.search(client, settings, FAKE_VECTOR)
    assert note.tipo == "clip"
