"""Both transports in actions.py: the native executors and the webhook fallback.

The routes register as an import side effect of `actions.py`; `main.py` imports it
for that and to register the executors. An "unused import" cleanup there would
silently take all three endpoints with it, and nothing else in the repo would notice.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from port_ocean.core.models import (
    WorkflowIntegrationActionConfig,
    WorkflowNodeRun,
    WorkflowNodeRunStatus,
)
from port_ocean.exceptions.execution_manager import ActionExecutionError

import actions
import main  # noqa: F401  -- importing main must be enough to serve the routes


class FakeRequest:
    """Just enough of a Starlette Request for actions.py's _authorize/_body."""

    def __init__(self, headers: dict[str, str] | None = None, body: dict | None = None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self) -> dict:
        return self._body


@pytest.fixture
def secret(monkeypatch):
    """Configure the shared webhook secret actions.py checks requests against.

    ocean.integration_config is a property that model_dumps a fresh dict on every
    access, so mutating one snapshot wouldn't stick: swap the whole ocean object
    actions.py sees instead.
    """
    fake_ocean = SimpleNamespace(integration_config={"port_webhook_secret": "shh"})
    monkeypatch.setattr(actions, "ocean", fake_ocean)
    return "shh"


def test_routes_registered() -> None:
    paths = [r.path for r in actions.ocean.router.routes]
    assert paths == ["/tasks/create", "/tasks/dispatch", "/tasks/cancel"], paths
    for name in ("create_task", "dispatch_task", "cancel_task"):
        assert hasattr(actions, name), f"{name} went missing"


EXECUTORS = (
    actions.CreateTaskExecutor,
    actions.DispatchTaskExecutor,
    actions.CancelTaskExecutor,
)


def test_spec_declares_every_executor() -> None:
    """Port only hands runs to actions the spec declares, and only with processing on."""
    with open(".port/spec.json") as f:
        spec = json.load(f)
    assert spec["actionsProcessingEnabled"] is True
    assert {a["name"] for a in spec["actions"]} == {e.ACTION_NAME for e in EXECUTORS}


# ── Auth: the endpoints are reachable from the public internet, so this fails closed ──


def test_authorize_rejects_when_no_secret_configured(monkeypatch) -> None:
    monkeypatch.setattr(actions, "ocean", SimpleNamespace(integration_config={}))
    with pytest.raises(HTTPException) as exc:
        actions._authorize(FakeRequest())
    assert exc.value.status_code == 503


def test_authorize_rejects_missing_or_wrong_secret(secret) -> None:
    for headers in ({}, {"x-stratonext-secret": "wrong"}):
        with pytest.raises(HTTPException) as exc:
            actions._authorize(FakeRequest(headers=headers))
        assert exc.value.status_code == 401


def test_authorize_accepts_correct_secret(secret) -> None:
    actions._authorize(FakeRequest(headers={"x-stratonext-secret": secret}))  # no raise


# ── Happy paths: create / dispatch / cancel against a fake SDK client ──


def _task(id_: str, status: str = "QUEUED", status_reason: str | None = None):
    return SimpleNamespace(
        id=id_, status=SimpleNamespace(value=status), status_reason=status_reason
    )


@pytest.fixture
def fake_client(monkeypatch):
    """A fake StratoNext SDK client recording every call made to it."""
    calls: list[tuple[str, dict]] = []

    client = SimpleNamespace(
        create_task=lambda **kw: _record(
            calls,
            "create_task",
            kw,
            SimpleNamespace(create_task=SimpleNamespace(task=_task("tsk_new"))),
        ),
        dispatch_task=lambda **kw: _record(
            calls,
            "dispatch_task",
            kw,
            SimpleNamespace(
                dispatch_task=SimpleNamespace(task=_task("tsk_1", "DISPATCHED"))
            ),
        ),
        cancel_task=lambda **kw: _record(
            calls,
            "cancel_task",
            kw,
            SimpleNamespace(
                cancel_task=SimpleNamespace(task=_task("tsk_1", "CANCELLED"))
            ),
        ),
    )

    async def get_project(**kw):
        calls.append(("get_project", kw))
        return SimpleNamespace(project=SimpleNamespace(workspace_id="wks_of_project"))

    client.get_project = get_project

    async def get_environment(**kw):
        calls.append(("get_environment", kw))
        return SimpleNamespace(environment=SimpleNamespace(workspace_id="wks_of_env"))

    client.get_environment = get_environment

    async def resolved(_config=None):
        return client

    monkeypatch.setattr(actions, "client_from_config", resolved)
    return calls


async def _record(calls: list, name: str, kwargs: dict, result):
    calls.append((name, kwargs))
    return result


async def test_create_task_happy_path(secret, fake_client) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret},
        body={"name": "n", "description": "d", "projectId": "prj_1"},
    )
    response = await actions.http_create_task(request)
    assert response == {"taskId": "tsk_new"}
    # No workspaceId given: inferred from the project with one lookup.
    assert [name for name, _ in fake_client] == ["get_project", "create_task"]
    created = fake_client[1][1]["input"]
    assert created.name == "n"
    assert created.project_id == "prj_1"
    assert created.workspace_id == "wks_of_project"


async def test_create_task_explicit_workspace_skips_lookup(secret, fake_client) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret},
        body={"name": "n", "description": "d", "workspaceId": "wks_9"},
    )
    await actions.http_create_task(request)
    assert [name for name, _ in fake_client] == ["create_task"]
    assert fake_client[0][1]["input"].workspace_id == "wks_9"


async def test_create_task_infers_workspace_from_environment(
    secret, fake_client
) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret},
        body={"name": "n", "description": "d", "environmentId": "env_1"},
    )
    await actions.http_create_task(request)
    assert [name for name, _ in fake_client] == ["get_environment", "create_task"]
    assert fake_client[1][1]["input"].workspace_id == "wks_of_env"


async def test_create_task_needs_workspace_or_project(secret, fake_client) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret}, body={"name": "n", "description": "d"}
    )
    with pytest.raises(HTTPException) as exc:
        await actions.http_create_task(request)
    assert exc.value.status_code == 400
    assert "workspaceId" in exc.value.detail
    assert not fake_client


async def test_create_task_missing_required_input(secret, fake_client) -> None:
    request = FakeRequest(headers={"x-stratonext-secret": secret}, body={"name": "n"})
    with pytest.raises(HTTPException) as exc:
        await actions.http_create_task(request)
    assert exc.value.status_code == 400
    assert not fake_client  # never reached the SDK


async def test_dispatch_task_happy_path_with_existing_task_id(
    secret, fake_client
) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret}, body={"taskId": "tsk_1"}
    )
    response = await actions.http_dispatch_task(request)
    assert response == {"taskId": "tsk_1", "status": "DISPATCHED", "statusReason": None}
    assert [name for name, _ in fake_client] == ["dispatch_task"]


async def test_dispatch_task_creates_when_no_task_id(secret, fake_client) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret},
        body={"name": "n", "description": "d", "workspaceId": "wks_9"},
    )
    response = await actions.http_dispatch_task(request)
    assert response["taskId"] == "tsk_1"
    assert [name for name, _ in fake_client] == ["create_task", "dispatch_task"]


async def test_cancel_task_happy_path(secret, fake_client) -> None:
    request = FakeRequest(
        headers={"x-stratonext-secret": secret}, body={"taskId": "tsk_1"}
    )
    response = await actions.http_cancel_task(request)
    assert response == {"taskId": "tsk_1", "status": "CANCELLED"}
    assert [name for name, _ in fake_client] == ["cancel_task"]


async def test_cancel_task_missing_task_id(secret, fake_client) -> None:
    request = FakeRequest(headers={"x-stratonext-secret": secret}, body={})
    with pytest.raises(HTTPException) as exc:
        await actions.http_cancel_task(request)
    assert exc.value.status_code == 400
    assert not fake_client


# ── Native executors: the same operations, driven by an INTEGRATION_ACTION run ──


def _run(action: str, props: dict) -> WorkflowNodeRun:
    return WorkflowNodeRun(
        id="run-1",
        status=WorkflowNodeRunStatus.IN_PROGRESS,
        config=WorkflowIntegrationActionConfig(
            type="INTEGRATION_ACTION",
            installationId="inst",
            integrationProvider="stratonext",
            integrationInvocationType=action,
            integrationActionExecutionProperties=props,
        ),
    )


@pytest.fixture
def port_client(monkeypatch):
    """Record what the executor reports back to Port."""
    reports: list[dict] = []

    async def report_run_completed(run, **kwargs):
        reports.append({"run": run.id, **kwargs})

    monkeypatch.setattr(
        actions,
        "ocean",
        SimpleNamespace(
            integration_config={},
            port_client=SimpleNamespace(report_run_completed=report_run_completed),
        ),
    )
    return reports


async def test_dispatch_executor_creates_then_dispatches(
    fake_client, port_client
) -> None:
    # "" is how Port renders an empty optional input; it must not reach the SDK.
    run = _run(
        "dispatch_task",
        {"name": "n", "description": "d", "workspaceId": "wks_9", "projectId": ""},
    )
    await actions.DispatchTaskExecutor().execute(run)
    assert [name for name, _ in fake_client] == ["create_task", "dispatch_task"]
    created = fake_client[0][1]["input"]
    assert created.project_id is None
    assert created.idempotency_key == "run-1", "run id should be the default key"
    assert run.output["taskId"] == "tsk_1"
    assert port_client == [
        {
            "run": "run-1",
            "success": True,
            "message": "StratoNext task tsk_1: DISPATCHED",
            "status_label": "Task dispatched",
        }
    ]


async def test_cancel_executor_missing_task_id(fake_client, port_client) -> None:
    with pytest.raises(ActionExecutionError) as exc:
        await actions.CancelTaskExecutor().execute(_run("cancel_task", {}))
    assert exc.value.status_label == "Invalid inputs"
    assert not fake_client  # never reached the SDK
    assert not port_client  # Ocean reports the failure, not us


async def test_executor_wraps_upstream_failure(monkeypatch, port_client) -> None:
    async def boom(*_, **__):
        raise RuntimeError("workspace not found")

    monkeypatch.setattr(actions, "cancel_task", boom)
    with pytest.raises(ActionExecutionError) as exc:
        await actions.CancelTaskExecutor().execute(_run("cancel_task", {"taskId": "t"}))
    assert "workspace not found" in str(exc.value)
    assert exc.value.status_label == "StratoNext failed"
