"""Entrypoint: serve the /integration/tasks/* webhooks. Long-running: Ctrl-C to stop.

ACTIONS_ONLY rather than the exporter's ONCE, because ONCE exits after a single
resync. ACTIONS_ONLY is the listener that stays up without resyncing: it leaves the
FastAPI app serving the routes in actions.py, which is all Port's WEBHOOK nodes need.
Port must be able to reach it, so expose it (ngrok, see the README).

ponytail: the event-loop shim is the same one run_once.py needs: Ocean 0.52
calls asyncio.get_event_loop() outside a running loop and Python 3.14 removed
that fallback. Delete both once Ocean fixes it.
"""

import asyncio

from port_ocean import run

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    run(".", config_override={"event_listener": {"type": "ACTIONS_ONLY"}})
