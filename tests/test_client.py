"""Self-checks for the non-trivial logic in client.py: the Relay paginator and the
token cache.
"""

import asyncio
from types import SimpleNamespace

import client
from client import paginate


class FakeNode:
    def __init__(self, id_: str) -> None:
        self.id = id_

    def model_dump(self, **_) -> dict:
        return {"id": self.id}


def _page(ids, has_next, end_cursor):
    return SimpleNamespace(
        nodes=[FakeNode(i) for i in ids],
        page_info=SimpleNamespace(has_next_page=has_next, end_cursor=end_cursor),
    )


async def check_paginate() -> None:
    """Pages are drained in cursor order; a null connection terminates."""
    pages = {None: _page(["a", "b"], True, "cur1"), "cur1": _page(["c"], False, None)}
    seen_cursors = []

    async def fetch(first, after):
        seen_cursors.append(after)
        return SimpleNamespace(things=pages[after])

    batches = [b async for b in paginate(fetch, lambda r: r.things)]
    assert batches == [[{"id": "a"}, {"id": "b"}], [{"id": "c"}]], batches
    assert seen_cursors == [None, "cur1"], seen_cursors

    # A null connection (no access / empty) terminates instead of exploding.
    async def empty(first, after):
        return SimpleNamespace(things=None)

    assert [b async for b in paginate(empty, lambda r: r.things)] == []


async def check_token_cache() -> None:
    """A cached M2M token is reused until its margin, then refetched."""
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": f"tok{len(calls)}", "expires_in": 3600}

    class FakeAsyncClient:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *a, **kw):
            calls.append(1)
            return FakeResponse()

    fake_ocean = SimpleNamespace(
        integration_config={
            "stratonext_auth_token_url": "https://auth.example.com/oauth2/agent-token",
            "stratonext_auth_client_id": "cid",
            "stratonext_auth_client_secret": "secret",
        }
    )
    real_httpx, real_ocean, real_cache = client.httpx, client.ocean, client._cached_m2m
    client.httpx = SimpleNamespace(AsyncClient=FakeAsyncClient)
    client.ocean = fake_ocean
    client._cached_m2m = None
    try:
        first = await client._client_credentials_token()
        second = await client._client_credentials_token()
        assert first == second, (first, second)
        assert len(calls) == 1, f"cache missed: {len(calls)} token calls"

        # Past the margin, it refetches.
        token, _ = client._cached_m2m
        client._cached_m2m = (token, -1.0)
        third = await client._client_credentials_token()
        assert len(calls) == 2, f"expired token not refetched: {len(calls)} calls"
        assert third != first, (third, first)
    finally:
        client.httpx, client.ocean, client._cached_m2m = (
            real_httpx,
            real_ocean,
            real_cache,
        )


def test_paginate() -> None:
    asyncio.run(check_paginate())


def test_token_cache() -> None:
    asyncio.run(check_token_cache())
