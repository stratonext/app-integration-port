# Contributing

Developer guide: running from source, how the pieces fit, and how to send a change. For
deploying and using the integration, see [README.md](README.md).

## Dev environment

Requires Python 3.14. Packaging is [Poetry](https://python-poetry.org/), driven through the
`Makefile`, with the same layout and targets as an integration in Port's Ocean monorepo.

```bash
make install             # installs Poetry if missing, then runtime deps plus the dev group
cp .env.example .env     # same variables as the README; fill in Port and StratoNext creds
make test                # pytest: resync handlers, webhook routes, executors, client, run_server
make lint                # poetry check, mypy, ruff, black, yamllint
make lint/fix            # black and ruff --fix
```

`make lint` and `make test` must pass before you open a PR. CI (`.github/workflows/ci.yml`)
runs the same targets. Add a dependency with `poetry add <name>` (or `poetry add --group dev`)
so `poetry.lock` moves with `pyproject.toml`.

StratoNext auth is the Cognito app identity only: `stratonextAuthTokenUrl`,
`stratonextAuthClientId` and `stratonextAuthClientSecret`, declared as integration
configuration in `.port/spec.json` so Port's install form collects them. In `.env` they are
the `OCEAN__INTEGRATION__CONFIG__STRATONEXT_AUTH_*` variables. There is no fixed-token path
and no fallback to the token cached by `stratonext login`: the container has no home
directory to read it from, and that token expires hourly anyway.

## Running from source

Three entrypoints. Run them from the repo root: Ocean resolves `pyproject.toml`, `.env` and
`.port/resources` relative to the working directory.

```bash
poetry run python run_server.py    # both jobs, continuously: what the container runs
poetry run python run_once.py      # one full resync of every kind, then exit
poetry run python run_actions.py   # serve the /integration/tasks/* webhooks only, Ctrl-C to stop
```

`run_once.py` is the fastest way to check a mapping change: run it, then look at Port's
**Builder** for the blueprints and **Catalog** for the entities. For `run_actions.py`, Port
still needs to reach you: `ngrok http 8000`, and point `STRATONEXT_INTEGRATION_URL` at the
tunnel.

> Upstream these are `ocean sail` / `ocean sail -O`, but Ocean 0.52 calls
> `asyncio.get_event_loop()` outside a running loop, which Python 3.14 removed. All three
> entrypoints install a loop first and are otherwise identical. Delete them once Ocean
> fixes that call.

`run_server.py` also reads two env vars (see `.env.example`):

| Variable | Default | What |
|---|---|---|
| `OCEAN__EVENT_LISTENER__TYPE` | `POLLING` | The listener that keeps the process up. `ONCE` exits after one resync; `WEBHOOKS_ONLY` never resyncs. |
| `OCEAN__SCHEDULED_RESYNC_INTERVAL` | `60` | Minutes between full resyncs. Unset, Ocean resyncs once at startup and then never again, so `run_server.py` fills in 60. Ocean ranks the process environment above `.env`. |

## How it fits together

| File | Does |
|---|---|
| `main.py` | One `@ocean.on_resync` handler per kind (six) |
| `client.py` | Auth (fixed token or Cognito app identity), `stratonext-sdk` wrapper, Relay paginator |
| `actions.py` | The `/integration/tasks/*` routes and the transport-agnostic `create_task` / `dispatch_task` / `cancel_task` |
| `push_mapping.py` | Push `port-app-config.yaml` to Port after editing it |
| `port-workflows.json` | The three Port workflow definitions |
| `.port/spec.json` | Integration metadata and config schema |
| `.port/resources/blueprints.json` | Blueprints Ocean creates on first run |
| `.port/resources/port-app-config.yaml` | Raw StratoNext JSON → Port entity, as JQ |
| `tests/` | pytest suite, one file per module |

### Export

`workspaces()` in `client.py` lists every workspace the integration identity can see.
Visibility is enablement: StratoNext adds a workspace to the identity when Port is enabled
there and removes it when disabled, so this list is exactly the enabled workspaces and the
container filters nothing. Ocean deletes entities that stop appearing on a resync, so
disabling cleans the catalog up by itself. Each handler in `main.py` pages through its StratoNext
Relay connection once per workspace (`paginate_workspaces`) and yields raw dicts; the JQ in
`port-app-config.yaml` turns them into entities. Environments and projects come back without
a `workspaceId`, so every node is stamped with the workspace it was listed under: that is
what the `workspace` relation on every blueprint keys off. There is no per-workspace setting.
Field-level notes:

- **Workspace**: the SDK's `get_workspaces` is fixed at 50 with no cursor, so a larger
  organization is truncated until the SDK gains a paginated query.

- **Task**: `assigneeId` can be a human, and `assigneeType` is often `null` even when an
  agent is assigned, so the agent relation keys off the `agt_` id prefix.
- **Scheduled job**: the list query doesn't return `instructions`, `environmentIds` or
  `deploymentIds` yet, so those stay null.
- **Report**: the list query carries no `body`; only `report(id:)` does. `body` stays null
  rather than costing a call per report.

`createMissingRelatedEntities: false` means a task referencing an agent that no longer comes
back from `agents(...)` is upserted with no agent relation rather than creating a stub agent
entity. Set it to `true` if you would rather see the stub (identifier only, no properties).

### Changing the data model

Two Ocean behaviours make this less obvious than it looks:

- **Blueprints are created once.** If *any* blueprint in `blueprints.json` already exists,
  Ocean skips creating all of them (`default_origin_setup.py:96`): it never patches. To add
  or change one after the first run, create/patch it in Port's Builder or via
  `/v1/blueprints`.
- **Port owns the mapping.** `port-app-config.yaml` only seeds Port on first run; after
  that Port's stored copy wins. After editing the YAML:
  ```bash
  poetry run python push_mapping.py     # PATCHes the mapping onto the integration in Port
  ```
  Equivalent to editing it by hand on the connector's page under **Context Lake → Connectors**.

### Dispatch

`actions.py` exposes `create_task`, `dispatch_task` and `cancel_task` over two transports.

**Native actions.** `.port/spec.json` declares the three actions and sets
`actionsProcessingEnabled`. Each has an executor class in `actions.py` (subclassing Ocean's
`AbstractExecutor`), registered from `main.py`. Ocean polls Port for `INTEGRATION_ACTION`
runs, hands each to the executor by `ACTION_NAME`, and reports success or failure back.
An executor raises `StratoNextActionError` for expected failures so Port shows the message
without a stack trace. On a workflow node run the result lands in `run.output`, which
downstream nodes read as `{{ .outputs.<node>.taskId }}`. The run id doubles as the
idempotency key, so a Port retry returns the original task.

Port only routes these runs to integrations in its own catalog, so they work once the
integration is published there (see below). Until then, use the webhook fallback.

**Webhook fallback.** Three routes served by the running integration:

| Endpoint | Body | Does |
|---|---|---|
| `POST /integration/tasks/create` | `name`, `description`, `workspaceId?`, `instructions?`, `projectId?`, `assigneeId?`, `environmentId(s)?`, `deploymentId(s)?`, `idempotencyKey?` | Creates a task |
| `POST /integration/tasks/dispatch` | `taskId`, **or** the create fields | Dispatches (creating first if no `taskId`) |
| `POST /integration/tasks/cancel` | `taskId` | Cancels a task |

- **Authorization.** Every request must carry an `X-StratoNext-Secret` header matching
  `OCEAN__INTEGRATION__CONFIG__PORT_WEBHOOK_SECRET`, compared with `hmac.compare_digest`.
  These endpoints create real work and are reachable from the public internet, so they
  **fail closed**: with no secret configured, every request is refused with a 503.
- **Workspace.** A task belongs to one workspace and there is no default. `workspaceId`
  is taken from the input, else from the project, else from the environment, each a single
  lookup (`_workspace_for`). With none of the three the request is a 400.
- **Idempotency.** Pass `idempotencyKey` (the workflow run id) so a Port retry returns the
  original task instead of creating a duplicate.
- **Empty inputs.** Port templates render an absent input as `""`; `_body` drops those so
  they don't look like values. `environmentId(s)` / `deploymentId(s)` accept a bare string
  or a list; `_as_list` normalises them, since a single picker renders as a string.
- **Timeout.** Port's `synchronized: true` webhook has a **10-second hard timeout**.
  Create+dispatch is two GraphQL calls and lands well inside that, but a slow StratoNext
  would trip it.

### Workflow definitions

Each entry in `port-workflows.json` is a `SELF_SERVE_TRIGGER` node (the form) plus `WEBHOOK`
nodes. Node order in the array is execution order; nodes pass data by template, not an edge
list: `stratonext_create_then_dispatch`'s second node reads
`{{ .outputs.create.response.data.taskId }}`.

No input carries a `default`: agent, project and deployment ids are tenant-specific, so
the form asks for them every time.

Project, Agent and Environment inputs are `format: entity` pickers, so they only populate
once the export has run at least once.

## Publishing to Port's catalog

Port's integration catalog is built from the [Ocean monorepo](https://github.com/port-labs/ocean):
getting listed means a PR adding `integrations/stratonext/` there, following its
[contributing guide](https://github.com/port-labs/ocean/blob/main/CONTRIBUTING.md). This repo
already has the monorepo's layout and tooling: a Poetry `pyproject.toml` and the standard
`Makefile`, `.port/spec.json` with `actions`, `.port/resources/`, `.port/examples/` (one
sample payload per kind), a towncrier `CHANGELOG.md`, a `-beta` version, and `make lint`
passing with the monorepo's `black`, `mypy`, `ruff` and `yamllint` settings. The monorepo's
CI also checks that `port_ocean` in `pyproject.toml` is at the latest PyPI release.

What still differs, and has to change in the monorepo copy only:

- **Python 3.12.** The monorepo installs and tests on 3.12. `stratonext-sdk` on PyPI
  requires 3.14, so it has to be republished with `requires-python >= 3.12` first. Once on
  3.12 the event-loop shim in the three entrypoints is unnecessary and `debug.py`
  (`from port_ocean import run; run()`) replaces them.
- **Release declaration.** A changeset under `integrations/stratonext/.ocean-release/`,
  or a manual `CHANGELOG.md` entry, is required on every integration PR.
- **Docs page.** The spec's `docs` field points at a page on docs.port.io, contributed
  through Port's docs repo.

## Pull requests

- Fork, then branch off `main`.
- Keep a PR to one change. Unrelated cleanups belong in their own PR.
- Run the tests and the linter first; add a self-check for any non-trivial logic you add.
- Say in the description what you ran against a real Port instance, if anything: most of
  this code can't be verified without one.
