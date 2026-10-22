"""Entrypoint: the long-running server a container runs.

One process doing both jobs the other two entrypoints split between them:

* the ``/integration/tasks/*`` webhook routes from ``actions.py``: run_actions.py's job
* a full resync of every kind registered in ``main.py``: run_once.py's job, repeated on
  a schedule instead of once

Upstream this is plain ``ocean sail``. Two env vars drive it (see .env.example):

* ``OCEAN__EVENT_LISTENER__TYPE=POLLING``: Ocean's default listener, and the one that
  keeps the process up while serving action runs and resyncing. ONCE and WEBHOOKS_ONLY
  both set ``should_run_actions = False``.
* ``OCEAN__SCHEDULED_RESYNC_INTERVAL``: minutes between full resyncs. Left unset, Ocean
  resyncs once at startup and then never again, which in a container is a catalog going
  quietly stale, so a default is filled in below.

run_once.py and run_actions.py stay for local and manual use; nothing here replaces them.

ponytail: the event-loop shim is the same one run_once.py and run_actions.py need: Ocean
0.52 calls asyncio.get_event_loop() outside a running loop (port_ocean/run.py:34) and
Python 3.14 removed that fallback. Delete all three once Ocean fixes that call.
"""

import asyncio
import os
from collections.abc import MutableMapping

from dotenv import dotenv_values
from port_ocean import run

RESYNC_INTERVAL_VAR = "OCEAN__SCHEDULED_RESYNC_INTERVAL"
DEFAULT_RESYNC_INTERVAL_MINUTES = "60"


def apply_defaults(environ: MutableMapping[str, str]) -> None:
    """Set a resync interval, unless one is already configured somewhere Ocean reads.

    Ocean ranks the process environment above `.env` (config/base.py
    settings_customise_sources), so a plain os.environ.setdefault would silently
    override an interval the user set only in `.env`.
    """
    if not {**dotenv_values(".env"), **environ}.get(RESYNC_INTERVAL_VAR):
        environ[RESYNC_INTERVAL_VAR] = DEFAULT_RESYNC_INTERVAL_MINUTES


if __name__ == "__main__":
    apply_defaults(os.environ)
    asyncio.set_event_loop(asyncio.new_event_loop())
    run(".")
