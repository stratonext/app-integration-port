"""Entrypoint: run one resync and exit.

ponytail: `ocean sail -O` is the upstream way to do this, but Ocean 0.52 calls
asyncio.get_event_loop() outside a running loop (port_ocean/run.py:33) and Python
3.14 removed that fallback. stratonext-sdk floors us at 3.14, so we install a loop
first and call run() ourselves. Delete this file once Ocean fixes that call.
"""

import asyncio

from port_ocean import run

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    run(".", config_override={"event_listener": {"type": "ONCE"}})
