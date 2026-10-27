# connectors/credentials.py
"""Credential resolution seam.

Connectors ask here for credentials; they never read a secret source directly.
Values arrive as environment variables, and a `.env` file at the repo root is
read here when a variable is not already in the environment. See `.env.example`
for the full list.

The environment wins over `.env`, so a value exported in the shell, injected by
a secret manager or set by a CI runner is used ahead of the file.

Named credentials.py (not secrets.py) to avoid shadowing the stdlib `secrets`.
"""
import os
from functools import cache
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

#: Warehouse endpoint. Not secret, but per-deployment, so it resolves the same
#: way as the password rather than being hardcoded.
_WAREHOUSE_VARS = {
    "host": "WAREHOUSE_HOST",
    "user": "WAREHOUSE_USER",
    "dbname": "WAREHOUSE_DB",
    "password": "WAREHOUSE_PASSWORD",
}

DEFAULT_WAREHOUSE_PORT = 5432


@cache
def _env_file_values():
    """Parse the repo-root `.env` into {name: value}. Empty if there is no file.

    Reads `NAME=value` lines. Skips blank lines and `#` comments, drops an
    optional leading `export`, and strips one pair of matching quotes. A name
    with an empty value counts as not set.
    """
    if not ENV_FILE.is_file():
        return {}
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            values[name] = value
    return values


def _lookup(name):
    """The raw value from the environment then `.env`, or None if set in neither."""
    value = os.environ.get(name)
    if value is None:
        value = _env_file_values().get(name)
    return value


def get_secret(name):
    """Read a value from the environment, then from the repo-root `.env`.

    Args:
        name: Env var name (e.g. "KLAVIYO_API_KEY").

    Returns:
        The value as a string.

    Raises:
        RuntimeError: the var is in neither source.
    """
    value = _lookup(name)
    if value is None:
        raise RuntimeError(
            f"{name} is not set. Add it to {ENV_FILE} (see .env.example), or "
            f"export it in the environment."
        )
    return value


def get_klaviyo_credentials(context=None):
    """Resolve Klaviyo credentials -> {"api_key"}.

    `context` is reserved for a future per-user lane and is ignored today.
    """
    return {"api_key": get_secret("KLAVIYO_API_KEY").strip()}


def get_warehouse_credentials(context=None):
    """Resolve the warehouse connection -> {"host", "port", "user", "dbname",
    "password"}.

    `context` is reserved for a future per-user lane and is ignored today.
    WAREHOUSE_PORT is optional and falls back to the Postgres default.

    Raises:
        RuntimeError: a required var is not set.
        ValueError: WAREHOUSE_PORT is set but is not a number.
    """
    resolved = {key: get_secret(var).strip() for key, var in _WAREHOUSE_VARS.items()}
    port = _lookup("WAREHOUSE_PORT")
    if port is None:
        resolved["port"] = DEFAULT_WAREHOUSE_PORT
    else:
        try:
            resolved["port"] = int(port)
        except ValueError:
            raise ValueError(f"WAREHOUSE_PORT must be a number, got {port!r}") from None
    return resolved
