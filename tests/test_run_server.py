"""Self-check for run_server.py's one piece of logic: the resync-interval default."""

from port_ocean.config.settings import IntegrationConfiguration

import run_server


def _apply(environ: dict[str, str], dotenv: dict[str, str | None]) -> dict[str, str]:
    real = run_server.dotenv_values
    run_server.dotenv_values = lambda _: dotenv
    try:
        run_server.apply_defaults(environ)
    finally:
        run_server.dotenv_values = real
    return environ


def test_resync_interval_defaults() -> None:
    var = run_server.RESYNC_INTERVAL_VAR

    # Nothing configured: a container still resyncs periodically instead of once.
    assert _apply({}, {}).get(var) == run_server.DEFAULT_RESYNC_INTERVAL_MINUTES

    # Already in the process environment: left alone.
    assert _apply({var: "15"}, {}).get(var) == "15"

    # Set only in .env: must NOT be shadowed. Ocean ranks os.environ above .env, so
    # writing the default here would override the user's value.
    assert var not in _apply({}, {var: "15"})

    # An empty value is not a value.
    assert _apply({}, {var: ""}).get(var) == run_server.DEFAULT_RESYNC_INTERVAL_MINUTES


def test_resync_interval_setting_name_matches_ocean() -> None:
    # The env var only works because Ocean names the setting this; catch a rename.
    assert "scheduled_resync_interval" in IntegrationConfiguration.model_fields
