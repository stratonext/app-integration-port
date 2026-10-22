"""Importing main.py runs @ocean.on_resync at import time, which needs the global
PortOcean context. Constructing Ocean() installs it: same bootstrap the standalone
self-checks use.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("OCEAN__PORT__CLIENT_ID", "x")
os.environ.setdefault("OCEAN__PORT__CLIENT_SECRET", "x")
os.environ.setdefault(
    "OCEAN__INTEGRATION__CONFIG__STRATONEXT_API_URL", "https://x/graphql"
)

from port_ocean.ocean import Ocean
from port_ocean.utils.signal import init_signal_handler

init_signal_handler()
Ocean()
