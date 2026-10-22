"""Push .port/resources/port-app-config.yaml to Port.

Port stores the mapping once the integration is initialized, and Ocean only seeds it
on first run (it skips default creation entirely if any blueprint already exists:
default_origin_setup.py:96). So after editing the YAML, run this to make Port match.

Equivalent to editing the mapping by hand in Port's Data sources page.

    poetry run python push_mapping.py
"""

import sys

import httpx
import yaml
from dotenv import dotenv_values

env = dotenv_values(".env")
base = str(env["OCEAN__PORT__BASE_URL"]).rstrip("/")
identifier = env["OCEAN__INTEGRATION__IDENTIFIER"]

token = httpx.post(
    f"{base}/v1/auth/access_token",
    json={
        "clientId": env["OCEAN__PORT__CLIENT_ID"],
        "clientSecret": env["OCEAN__PORT__CLIENT_SECRET"],
    },
    timeout=30,
).json()["accessToken"]

with open(".port/resources/port-app-config.yaml") as f:
    config = yaml.safe_load(f)
resp = httpx.patch(
    f"{base}/v1/integration/{identifier}",
    headers={"Authorization": f"Bearer {token}"},
    json={"config": config},
    timeout=30,
)
if resp.is_error:
    sys.exit(f"Failed to push mapping: {resp.status_code} {resp.text}")

stored = resp.json()["integration"]["config"]["resources"]
print(f"Pushed to {identifier}: {[r['kind'] for r in stored]}")
