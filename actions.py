"""Everything Port can ask us to do: create, dispatch or cancel a StratoNext task.

Two transports over the same three operations:

* **Native actions** (`INTEGRATION_ACTION` runs). Declared in `.port/spec.json`, so
  once the integration is installed from Port's catalog a workflow node can call
  `create_task` / `dispatch_task` / `cancel_task` directly. Port authenticates the
  run; no shared secret is involved.
* **Webhook routes** (`/integration/tasks/*`), the fallback for a self-hosted install
  that Port does not list yet. A workflow `WEBHOOK` node calls them with a shared
  `X-StratoNext-Secret` header. Port validates `integrationProvider` against its own
  catalog, so native actions only work after the integration is published there.

Both are fire-and-forget: a call returns as soon as StratoNext accepts the task. It
does not wait for the agent to finish.
"""

import hmac
from typing import Any

from fastapi import HTTPException, Request
from loguru import logger
from port_ocean.context.ocean import ocean
from port_ocean.core.handlers.actions.abstract_executor import AbstractExecutor
from port_ocean.core.models import IntegrationRun, WorkflowNodeRun
from port_ocean.exceptions.execution_manager import ActionExecutionError
from stratonext_sdk.input_types import CreateTaskInput

from client import client_from_config


class MissingInput(Exception):
    """A required action input was absent. Raised before StratoNext is touched."""


def _upstream(error: Exception) -> HTTPException:
    """Surface why StratoNext refused, rather than a bare 500 in the Port run log."""
    logger.error(f"StratoNext call failed: {error}")
    return HTTPException(502, f"StratoNext rejected the request: {error}")


def _require(props: dict[str, Any], *names: str) -> list[str]:
    """Inputs come from a Port user: validate at the boundary, not downstream."""
    missing = [n for n in names if not props.get(n)]
    if missing:
        raise MissingInput(f"Missing required input(s): {', '.join(missing)}")
    return [props[n] for n in names]


def _present(payload: dict[str, Any]) -> dict[str, Any]:
    """Port renders an absent input as "" or null, which must not look like a value."""
    return {k: v for k, v in payload.items() if v not in ("", None)}


# ── The three operations ──


def _as_list(value: Any) -> list[str] | None:
    """Port templates render a single picker as a string; the API wants a list."""
    if not value:
        return None
    return value if isinstance(value, list) else [value]


async def _workspace_for(client: Any, props: dict[str, Any]) -> str:
    """The workspace a new task belongs to.

    One container serves the whole organization, so there is no default workspace. It is
    given explicitly, or inferred from the project or environment the task targets: both
    belong to exactly one workspace, and the existing workflow forms already pick one.
    """
    if workspace := props.get("workspaceId"):
        return str(workspace)
    if project_id := props.get("projectId"):
        project = (await client.get_project(project_id=project_id)).project
        return str(project.workspace_id)
    if environment_id := _as_list(
        props.get("environmentIds") or props.get("environmentId")
    ):
        env = (
            await client.get_environment(environment_id=environment_id[0])
        ).environment
        return str(env.workspace_id)
    raise MissingInput(
        "Missing required input(s): workspaceId (or projectId / environmentId to infer it)"
    )


async def create_task(props: dict[str, Any], idempotency_key: str | None) -> str:
    name, description = _require(props, "name", "description")
    client = await client_from_config()
    result = await client.create_task(
        input=CreateTaskInput(
            name=name,
            description=description,
            workspaceId=await _workspace_for(client, props),
            projectId=props.get("projectId"),
            instructions=props.get("instructions"),
            assigneeId=props.get("assigneeId"),
            # Targets the task at an environment directly, so the agent can resolve
            # that environment's operations role even with no project deployment.
            environmentIds=_as_list(
                props.get("environmentIds") or props.get("environmentId")
            ),
            # Preferred over environmentIds when the work belongs to a project: the agent
            # resolves each deployment's environment (and its ops role) via its pre-start
            # hook chain, and gets the stack/region/graph along with it.
            deploymentIds=_as_list(
                props.get("deploymentIds") or props.get("deploymentId")
            ),
            verificationCriteria=props.get("verificationCriteria"),
            # Port re-delivers on an unacknowledged claim or a webhook retry; the key
            # makes the retry return the original task instead of a duplicate.
            idempotencyKey=idempotency_key,
        )
    )
    return result.create_task.task.id


async def dispatch_task(task_id: str) -> dict[str, Any]:
    client = await client_from_config()
    task = (await client.dispatch_task(task_id=task_id)).dispatch_task.task
    return {
        "taskId": task.id,
        "status": task.status.value,
        "statusReason": task.status_reason,
    }


async def cancel_task(task_id: str) -> dict[str, Any]:
    client = await client_from_config()
    task = (await client.cancel_task(task_id=task_id)).cancel_task.task
    return {"taskId": task.id, "status": task.status.value}


# ── Native actions: INTEGRATION_ACTION runs Port hands to this integration ──


class StratoNextActionError(ActionExecutionError):
    """An expected failure. Ocean reports the message to Port without a stack trace."""

    DEFAULT_STATUS_LABEL = "StratoNext failed"


class _TaskExecutor(AbstractExecutor):
    """One StratoNext call per run, then report completion.

    StratoNext exposes no rate-limit headers, so the rate-limit hooks say "never".
    """

    WEBHOOK_PROCESSOR_CLASS = None
    WEBHOOK_PATH = ""
    DONE_LABEL: str

    async def is_close_to_rate_limit(self, run: IntegrationRun) -> bool:
        return False

    async def get_remaining_seconds_until_rate_limit(
        self, run: IntegrationRun
    ) -> float:
        return 0.0

    async def _run(self, props: dict[str, Any], run_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def execute(self, run: IntegrationRun) -> None:
        props = _present(run.execution_properties)
        try:
            result = await self._run(props, run.id)
        except MissingInput as error:
            raise StratoNextActionError(
                str(error), status_label="Invalid inputs"
            ) from error
        except Exception as error:
            logger.error(f"StratoNext call failed: {error}")
            raise StratoNextActionError(
                f"StratoNext rejected the request: {error}"
            ) from error
        if isinstance(run, WorkflowNodeRun):
            # Downstream nodes read these as {{ .outputs.<node>.taskId }} etc.
            run.output = result
        await ocean.port_client.report_run_completed(
            run,
            success=True,
            message=f"StratoNext task {result['taskId']}: {result.get('status', 'CREATED')}",
            status_label=self.DONE_LABEL,
        )


class CreateTaskExecutor(_TaskExecutor):
    ACTION_NAME = "create_task"
    DONE_LABEL = "Task created"

    async def _run(self, props: dict[str, Any], run_id: str) -> dict[str, Any]:
        # The run id is a natural idempotency key: Port retries re-use the same run.
        key = props.get("idempotencyKey") or run_id
        return {"taskId": await create_task(props, key)}


class DispatchTaskExecutor(_TaskExecutor):
    ACTION_NAME = "dispatch_task"
    DONE_LABEL = "Task dispatched"

    async def _run(self, props: dict[str, Any], run_id: str) -> dict[str, Any]:
        task_id = props.get("taskId") or await create_task(
            props, props.get("idempotencyKey") or run_id
        )
        return await dispatch_task(task_id)


class CancelTaskExecutor(_TaskExecutor):
    ACTION_NAME = "cancel_task"
    DONE_LABEL = "Task cancelled"

    async def _run(self, props: dict[str, Any], run_id: str) -> dict[str, Any]:
        (task_id,) = _require(props, "taskId")
        return await cancel_task(task_id)


def register_action_executors() -> None:
    """Called once from main.py. Ocean skips it under a listener that runs no actions."""
    for executor in (
        CreateTaskExecutor(),
        DispatchTaskExecutor(),
        CancelTaskExecutor(),
    ):
        ocean.register_action_executor(executor)


# ── Webhook routes: the fallback for Port workflow WEBHOOK nodes ──


def _authorize(request: Request) -> None:
    """Shared secret, fails closed.

    This endpoint creates real work in StratoNext and is reachable from the public
    internet via the tunnel, so an unset secret denies rather than allows.
    """
    expected = ocean.integration_config.get("port_webhook_secret")
    if not expected:
        raise HTTPException(503, "portWebhookSecret is not configured")
    if not hmac.compare_digest(
        request.headers.get("x-stratonext-secret", ""), expected
    ):
        raise HTTPException(401, "Bad or missing X-StratoNext-Secret header")


async def _body(request: Request) -> dict[str, Any]:
    _authorize(request)
    try:
        payload = await request.json()
    except Exception as exc:  # any parse failure is a client error
        raise HTTPException(400, "Body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Body must be a JSON object")
    return _present(payload)


@ocean.router.post("/tasks/create")
async def http_create_task(request: Request) -> dict[str, Any]:
    props = await _body(request)
    try:
        task_id = await create_task(props, props.get("idempotencyKey"))
    except MissingInput as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        raise _upstream(error) from error
    logger.info(f"Created StratoNext task {task_id}")
    return {"taskId": task_id}


@ocean.router.post("/tasks/dispatch")
async def http_dispatch_task(request: Request) -> dict[str, Any]:
    props = await _body(request)
    task_id = props.get("taskId")
    try:
        if not task_id:
            task_id = await create_task(props, props.get("idempotencyKey"))
        result = await dispatch_task(task_id)
    except MissingInput as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        raise _upstream(error) from error
    logger.info(f"Dispatched StratoNext task {task_id} ({result['status']})")
    return result


@ocean.router.post("/tasks/cancel")
async def http_cancel_task(request: Request) -> dict[str, Any]:
    props = await _body(request)
    if not props.get("taskId"):
        raise HTTPException(400, "Missing required input: taskId")
    try:
        return await cancel_task(props["taskId"])
    except Exception as error:
        raise _upstream(error) from error
