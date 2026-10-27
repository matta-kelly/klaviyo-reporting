# connectors/warehouse/__init__.py
"""Warehouse connector.

Reaches one external system: the Postgres warehouse that holds the Klaviyo
models this report reads. Hidden infra, not a tool. The endpoint and the
password both resolve through connectors.credentials, from the environment or
`.env`, so nothing about the deployment is baked into the code.

The role needs read access to the `int` and `proc` schemas and nothing else.

TLS: the connection is made with sslmode=verify-full, so the server identity is
checked, and sslrootcert points at certifi's public-root bundle. Why certifi
and not "system": psycopg2-binary ships its own OpenSSL with no default CA
store, so sslrootcert=system finds nothing and verification fails (psql works
because it uses the OS libpq/OpenSSL). certifi carries the public roots and is
portable across hosts. A warehouse serving a private or self-signed cert needs
sslrootcert pointed at its own CA instead.
"""
import certifi
import psycopg2

from connectors.credentials import get_warehouse_credentials


def get_connection(credentials=None):
    """Connect to the warehouse with verified TLS.

    Args:
        credentials: optional {"host", "port", "user", "dbname", "password"} to
            inject. Defaults to the resolver, which reads the environment and
            then `.env`.

    Returns:
        An open psycopg2 connection. The caller owns closing it.

    Raises:
        RuntimeError: a required credential is not set.
        psycopg2.OperationalError: the warehouse is unreachable, TLS fails to
            verify, or the connection is rejected.
    """
    creds = credentials or get_warehouse_credentials()
    return psycopg2.connect(
        host=creds["host"],
        port=creds["port"],
        user=creds["user"],
        password=creds["password"],
        dbname=creds["dbname"],
        sslmode="verify-full",
        sslrootcert=certifi.where(),
    )
