"""The @ocean.on_resync handlers: each must page the right SDK call under every workspace
and hand Port the raw node dicts.

Only the SDK client is faked (same shape as test_client.py): the real `paginate` runs,
so a handler wired to the wrong connection field or dropping the cursor fails here.
"""

from types import SimpleNamespace

import pytest

import main
from main import ObjectKind

# handler, SDK method it wraps, connection field on the response
HANDLERS = [
    (main.on_environment_resync, "get_environments", "environments"),
    (main.on_agent_resync, "get_agents", "agents"),
    (main.on_project_resync, "get_projects", "projects"),
    (main.on_task_resync, "get_tasks", "tasks"),
    (main.on_scheduled_job_resync, "get_jobs", "jobs"),
    (main.on_report_resync, "get_reports", "reports"),
]

WORKSPACES = ["wks_1", "wks_2"]


class FakeNode:
    def __init__(self, id_: str) -> None:
        self.id = id_

    def model_dump(self, **kwargs) -> dict:
        # Port's JQ mappings depend on these two; a handler that loses them is a bug.
        assert kwargs == {"by_alias": True, "mode": "json"}, kwargs
        return {"identifier": self.id}


def _page(ids, has_next, end_cursor):
    return SimpleNamespace(
        nodes=[FakeNode(i) for i in ids],
        page_info=SimpleNamespace(has_next_page=has_next, end_cursor=end_cursor),
    )


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install a stub SDK client; returns the list every call to it is recorded in."""
    calls: list[dict] = []

    def install(method: str, field: str, pages: dict):
        async def fetch(**kw):
            calls.append(kw)
            return SimpleNamespace(**{field: pages[kw["after"]]})

        async def get_workspaces():
            # Real workspace nodes dump an `id`, which is what paginate_workspaces reads.
            nodes = [
                SimpleNamespace(model_dump=lambda w=w, **_: {"id": w, "name": w})
                for w in WORKSPACES
            ]
            return SimpleNamespace(workspaces=SimpleNamespace(nodes=nodes))

        client = SimpleNamespace(**{method: fetch, "get_workspaces": get_workspaces})
        monkeypatch.setattr(main, "client_from_config", lambda: _resolved(client))
        return calls

    return install


async def _resolved(value):
    return value


@pytest.mark.parametrize("handler,method,field", HANDLERS, ids=[h[1] for h in HANDLERS])
async def test_resync_yields_node_dicts(fake_sdk, handler, method, field):
    pages = {
        None: _page(["a", "b"], True, "cur1"),
        "cur1": _page(["c"], False, None),
    }
    calls = fake_sdk(method, field, pages)

    batches = [batch async for batch in handler(field)]

    # Every workspace is drained in turn, and each node is stamped with its workspace:
    # that is what the Port `workspace` relation keys off.
    def expected(ws):
        return [
            [
                {"identifier": "a", "workspaceId": ws},
                {"identifier": "b", "workspaceId": ws},
            ],
            [{"identifier": "c", "workspaceId": ws}],
        ]

    assert batches == expected("wks_1") + expected("wks_2")
    assert [c["workspace_id"] for c in calls] == ["wks_1", "wks_1", "wks_2", "wks_2"]
    assert [c["after"] for c in calls] == [None, "cur1", None, "cur1"]


@pytest.mark.parametrize("handler,method,field", HANDLERS, ids=[h[1] for h in HANDLERS])
async def test_resync_empty_connection(fake_sdk, handler, method, field):
    """No access / nothing there: yield nothing rather than explode."""
    fake_sdk(method, field, {None: None})

    assert [batch async for batch in handler(field)] == []


async def test_workspace_resync(fake_sdk):
    """Workspaces are a single page, not scoped to anything."""
    fake_sdk("unused", "unused", {})
    assert [b async for b in main.on_workspace_resync("workspace")] == [
        [{"id": "wks_1", "name": "wks_1"}, {"id": "wks_2", "name": "wks_2"}]
    ]


async def test_on_start_tolerates_no_enabled_workspace(fake_sdk, monkeypatch):
    """Nothing enabled yet is a valid state: warn, never crash-loop the container."""
    fake_sdk("unused", "unused", {})
    await main.on_start()  # two workspaces visible

    monkeypatch.setattr(main, "workspaces", _empty)
    await main.on_start()  # none visible: still no raise


async def _empty(_client):
    return []


def test_every_kind_has_a_handler():
    """A new ObjectKind added without a test here is the failure this catches."""
    assert len(HANDLERS) + 1 == len(ObjectKind)  # +1: workspace, tested on its own
