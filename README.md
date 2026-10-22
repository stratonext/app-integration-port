# StratoNext → Port

A [Port Ocean](https://github.com/port-labs/ocean) integration that keeps Port's software
catalog in sync with a StratoNext organization, and lets Port workflows put StratoNext
agents to work.

- **Export**: every workspace that enabled the Port integration, and per workspace the
  environments, agents, projects, tasks, scheduled jobs and reports, upserted as Port
  entities on a schedule. One container covers the whole organization.
- **Dispatch**: a self-service form in Port creates, dispatches or cancels a StratoNext
  task, so a button in Port turns into an agent doing real work.

Developers: see [CONTRIBUTING.md](CONTRIBUTING.md) for running from source, tests, and how
the pieces fit.

## 1. Run the container

```bash
cp .env.example .env     # fill it in, see below
docker compose up --build
```

Or without compose:

```bash
docker build -t stratonext-port .
docker run --rm --env-file .env -p 8000:8000 stratonext-port
```

The container listens on `:8000`, resyncs the catalog on a schedule, and serves the
dispatch endpoints. Port must be able to reach that URL for dispatch to work, so put it
behind your usual ingress (on a laptop, `ngrok http 8000`).

### Configuration

Everything is set in `.env`. Required:

| Variable | What |
|---|---|
| `OCEAN__PORT__CLIENT_ID` / `OCEAN__PORT__CLIENT_SECRET` | Port app → **Settings → Credentials** |
| `OCEAN__PORT__BASE_URL` | `https://api.us.port.io` (US) or `https://api.port.io` (EU) |
| `OCEAN__INTEGRATION__CONFIG__STRATONEXT_API_URL` | StratoNext GraphQL endpoint |
| `OCEAN__INTEGRATION__CONFIG__STRATONEXT_AUTH_DOMAIN` | Cognito domain of the StratoNext machine identity |
| `OCEAN__INTEGRATION__CONFIG__STRATONEXT_AUTH_CLIENT_ID` | Client ID of the machine identity |
| `OCEAN__INTEGRATION__CONFIG__STRATONEXT_AUTH_CLIENT_SECRET` | Client secret of the machine identity |
| `OCEAN__INTEGRATION__CONFIG__PORT_WEBHOOK_SECRET` | Shared secret for the webhook dispatch endpoints. **Unset, every webhook request is refused.** Not needed for the native actions. |

Optional:

| Variable | Default | What |
|---|---|---|
| `OCEAN__SCHEDULED_RESYNC_INTERVAL` | `60` | Minutes between full catalog resyncs. |
| `OCEAN__INTEGRATION__IDENTIFIER` | `stratonext-demo` | Name of the integration in Port. |

## 2. Enable workspaces in StratoNext

Integrations in StratoNext are enabled per workspace, and the container syncs exactly the
workspaces that enabled Port. Visibility is enablement: the integration identity in `.env`
can see a workspace only while Port is enabled there.

1. An organization admin creates the Port integration identity once, and puts its three
   values in `.env` (the `STRATONEXT_AUTH_*` variables above).
2. Each workspace admin enables **Port** in their workspace's integrations.
3. The container picks the workspace up on its next resync (every 60 minutes by default) or
   on restart. Nothing to configure on the container.

Disabling Port in a workspace removes that workspace's entities from Port on the next
resync. The container logs, at startup, which workspaces it can see.

## 3. Connect Port

The first run creates the seven blueprints and the data mapping in Port automatically, and
the integration appears under **Data sources**. The export needs nothing more: it covers
every workspace that enabled it.

Dispatching tasks from Port needs one of the two paths described in
[Dispatching tasks from Port](#dispatching-tasks-from-port). For the webhook fallback, in Port:

1. Under **Settings → Secrets**, add two secrets:
   - `STRATONEXT_INTEGRATION_URL`: the container's public base URL, no trailing slash
   - `STRATONEXT_WEBHOOK_SECRET`: the same value as `PORT_WEBHOOK_SECRET` in `.env`
2. Import the workflows from `port-workflows.json`: paste each one into Port's workflow
   JSON editor, or POST it to `/v1/workflows`.

After the first resync the Project, Agent and Environment inputs on the forms become
dropdowns of real StratoNext resources.

## What you get in the catalog

| Blueprint | Relations | What it is |
|---|---|---|
| `stratonextWorkspace` | none | A workspace in the organization. Everything below belongs to one. |
| `stratonextEnvironment` | workspace | A cloud account/region an agent can operate in |
| `stratonextAgent` | workspace, environment | An AI operator registered in the workspace |
| `stratonextProject` | workspace | A unit of work agents and humans collaborate on |
| `stratonextTask` | workspace, project, agent | A unit of work assigned to an agent or a human |
| `stratonextScheduledJob` | workspace, project, agent | A recurring task an agent runs on a cron schedule |
| `stratonextReport` | workspace, projects | An incident, investigation or write-up against one or more projects |

Tasks dispatched from Port appear in the catalog on the next resync.

## Dispatching tasks from Port

There are two ways, with the same three operations behind them.

**Native actions.** The integration declares `create_task`, `dispatch_task` and
`cancel_task` as Port actions. A workflow node of type integration action calls them
directly, Port authenticates the run itself, and no secrets or workflow import are needed.
This is the path once the integration is installed from Port's catalog. Until Port lists
it, Port rejects `stratonext` as an integration provider, which is why the fallback exists.

**Webhook fallback.** `port-workflows.json` ships five ready-to-use workflows whose nodes
call the integration's `/integration/tasks/*` endpoints with the shared secret from
`PORT_WEBHOOK_SECRET`. Set it up as described in [Connect Port](#3-connect-port).

General purpose:

| Workflow | What |
|---|---|
| `stratonext_dispatch_task` | Create and dispatch a task in one step. Asks for name, description, optional instructions, workspace, project and agent. The workspace is optional when a project is chosen. |
| `stratonext_create_then_dispatch` | Same inputs, split in two steps so an approval node can gate the dispatch. |
| `stratonext_cancel_task` | Cancel a queued or running task. |

Showcases, where the task content is fixed and the form only exposes a few knobs:

| Workflow | What |
|---|---|
| `stratonext_aws_cost_report` | Read-only AWS cost report for one environment, broken down by service and compared against the preceding period. Inputs: environment, lookback days, granularity, agent. |
| `stratonext_investigate_service` | SRE triage of a service's Lambda metrics and logs. Appears as a button on each `stratonextProject` entity page with the project prefilled. Inputs: lookback hours, agent. |

All dispatches are fire-and-forget: the workflow completes as soon as StratoNext accepts
the task, not when the agent finishes. Follow progress on the task in StratoNext or, after
the next resync, in Port.

### Targeting an environment

Which field a workflow sends decides what AWS access the agent gets:

- **Environment**: targets an environment directly. Use when the work isn't tied to a
  project.
- **Deployment**: preferred for project work. The agent resolves the environment plus the
  stack, region and resource graph from the deployment.

## Not included

Waiting for a dispatched task to finish, project→environment links within StratoNext, and
deployments as entities.

---

Licensed under the MIT License, see [LICENSE](LICENSE).
