from enum import StrEnum

from loguru import logger
from port_ocean.context.ocean import ocean
from port_ocean.core.ocean_types import ASYNC_GENERATOR_RESYNC_TYPE

import actions  # registers the /integration/tasks/* routes on import
from client import client_from_config, paginate_workspaces, workspaces


class ObjectKind(StrEnum):
    WORKSPACE = "workspace"
    ENVIRONMENT = "environment"
    AGENT = "agent"
    PROJECT = "project"
    TASK = "task"
    SCHEDULED_JOB = "scheduledJob"
    REPORT = "report"


@ocean.on_start()
async def on_start() -> None:
    """Say who we are and what we can see, so an empty catalog is explainable from the log.

    Visibility is enablement: a workspace that turns on the Port integration becomes
    visible to this identity, and only those workspaces are synced. Zero is a valid state
    (nothing enabled yet), so this warns instead of raising.
    """
    client = await client_from_config()
    # ponytail: no callerIdentity call: the SDK's model rejects the machine identity's
    # kind (APP, not AGENT/USER) and the config already says who we are. Reinstate once
    # stratonext-sdk knows APP.
    who = ocean.integration_config["stratonext_auth_client_id"]
    visible = await workspaces(client)
    logger.info(
        f"StratoNext client {who} sees {len(visible)} workspace(s): "
        f"{', '.join(w['name'] for w in visible) or 'none'}"
    )
    if not visible:
        logger.warning(
            "No workspace has the Port integration enabled for this identity. Enable it "
            "in StratoNext and the next resync will pick it up."
        )


@ocean.on_resync(ObjectKind.WORKSPACE)
async def on_workspace_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    yield await workspaces(client)


@ocean.on_resync(ObjectKind.ENVIRONMENT)
async def on_environment_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    fetch = lambda ws, **kw: client.get_environments(workspace_id=ws, **kw)
    async for batch in paginate_workspaces(client, fetch, lambda r: r.environments):
        yield batch


@ocean.on_resync(ObjectKind.AGENT)
async def on_agent_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    fetch = lambda ws, **kw: client.get_agents(workspace_id=ws, **kw)
    async for batch in paginate_workspaces(client, fetch, lambda r: r.agents):
        yield batch


@ocean.on_resync(ObjectKind.PROJECT)
async def on_project_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    fetch = lambda ws, **kw: client.get_projects(workspace_id=ws, **kw)
    async for batch in paginate_workspaces(client, fetch, lambda r: r.projects):
        yield batch


@ocean.on_resync(ObjectKind.TASK)
async def on_task_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    fetch = lambda ws, **kw: client.get_tasks(workspace_id=ws, **kw)
    async for batch in paginate_workspaces(client, fetch, lambda r: r.tasks):
        yield batch


@ocean.on_resync(ObjectKind.SCHEDULED_JOB)
async def on_scheduled_job_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    fetch = lambda ws, **kw: client.get_jobs(workspace_id=ws, **kw)
    async for batch in paginate_workspaces(client, fetch, lambda r: r.jobs):
        yield batch


@ocean.on_resync(ObjectKind.REPORT)
async def on_report_resync(kind: str) -> ASYNC_GENERATOR_RESYNC_TYPE:
    client = await client_from_config()
    fetch = lambda ws, **kw: client.get_reports(workspace_id=ws, **kw)
    async for batch in paginate_workspaces(client, fetch, lambda r: r.reports):
        yield batch


actions.register_action_executors()
