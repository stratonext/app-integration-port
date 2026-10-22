"""Thin async wrapper over stratonext-sdk for the Ocean integration."""

import base64
import time
from collections.abc import AsyncGenerator, Callable
from functools import partial
from typing import Any

import httpx
from port_ocean.context.ocean import ocean
from stratonext_sdk import Client

PAGE_SIZE = 100
# Refresh this far before actual expiry: same margin the stratonext CLI uses.
EXPIRY_MARGIN_SECONDS = 60

_cached_m2m: tuple[str, float] | None = None


async def _client_credentials_token() -> str:
    """Cognito machine-to-machine token, cached until just before it expires.

    A form-encoded POST to {domain}/oauth2/token with HTTP Basic auth. No
    `audience`/`scope` params: a client_credentials grant issues the scopes fixed
    on the app client and Cognito's token endpoint rejects them.
    """
    global _cached_m2m
    if _cached_m2m and time.monotonic() < _cached_m2m[1]:
        return _cached_m2m[0]

    config = ocean.integration_config
    domain = config["stratonext_auth_domain"]
    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    basic = base64.b64encode(
        f"{config['stratonext_auth_client_id']}:"
        f"{config['stratonext_auth_client_secret']}".encode()
    ).decode()

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            f"https://{domain}/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    resp.raise_for_status()
    body = resp.json()
    _cached_m2m = (
        body["access_token"],
        time.monotonic() + body["expires_in"] - EXPIRY_MARGIN_SECONDS,
    )
    return _cached_m2m[0]


async def _token() -> str:
    """Bearer token for the StratoNext API, from the machine identity.

    The three values are integration configuration declared in .port/spec.json, so
    Port's install form collects them like any other setting. There is no fallback
    to the token cached by `stratonext login`: a container has no home directory to
    read it from, and that token expires hourly anyway.
    """
    if ocean.integration_config.get("stratonext_auth_client_id"):
        return await _client_credentials_token()
    raise RuntimeError(
        "No StratoNext credentials: set stratonextAuthDomain, stratonextAuthClientId "
        "and stratonextAuthClientSecret on the integration "
        "(OCEAN__INTEGRATION__CONFIG__STRATONEXT_AUTH_* in .env)."
    )


async def create_client(api_url: str) -> Client:
    return Client(url=api_url, headers={"Authorization": f"Bearer {await _token()}"})


async def client_from_config() -> Client:
    """Client built from the integration's own config: shared by resync and actions."""
    return await create_client(ocean.integration_config["stratonext_api_url"])


async def paginate(
    fetch: Callable[..., Any], connection_of: Callable[[Any], Any]
) -> AsyncGenerator[list[dict[str, Any]]]:
    """Drain a Relay connection, yielding one batch of raw dicts per page.

    `by_alias` gives Port the camelCase keys the JQ mappings expect; `mode="json"`
    renders pydantic enums and the createdAt DateTime scalar as plain strings.
    """
    cursor: str | None = None
    while True:
        connection = connection_of(await fetch(first=PAGE_SIZE, after=cursor))
        if connection is None:
            return
        yield [
            node.model_dump(by_alias=True, mode="json")
            for node in (connection.nodes or [])
        ]
        if not connection.page_info.has_next_page:
            return
        cursor = connection.page_info.end_cursor


async def workspaces(client: Client) -> list[dict[str, Any]]:
    """Every workspace the machine identity can see: the whole organization, in practice.

    One container syncs them all; there is no per-workspace configuration.
    """
    # ponytail: the SDK's GetWorkspaces query is fixed at `first: 50` with no cursor
    # argument, so an organization with more workspaces than that is truncated here.
    # Upgrade path: a paginated get_workspaces in stratonext-sdk, then `paginate` it.
    connection = (await client.get_workspaces()).workspaces
    nodes = (connection.nodes if connection else None) or []
    return [node.model_dump(by_alias=True, mode="json") for node in nodes]


async def paginate_workspaces(
    client: Client,
    fetch_for: Callable[..., Any],
    connection_of: Callable[[Any], Any],
) -> AsyncGenerator[list[dict[str, Any]]]:
    """`paginate` once per workspace, `fetch_for(workspace_id, first=, after=)` each time.

    Environments and projects come back without a workspaceId, so every node is stamped
    with the workspace it was listed under: that is what the Port relation keys off.
    """
    for workspace in await workspaces(client):
        async for batch in paginate(partial(fetch_for, workspace["id"]), connection_of):
            for node in batch:
                node.setdefault("workspaceId", workspace["id"])
            yield batch
