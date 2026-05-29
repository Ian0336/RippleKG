"""ArangoDB connection. Creates the target database on first connect.

Retries on connect because the api container may come up a beat before
ArangoDB is fully ready (the compose healthcheck covers the common case).
"""
import time

from arango import ArangoClient
from arango.database import StandardDatabase

from ripplekg.config import settings


def get_db(retries: int = 10, delay: float = 2.0) -> StandardDatabase:
    client = ArangoClient(hosts=settings.arango_url)
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            sys_db = client.db(
                "_system",
                username=settings.arango_user,
                password=settings.arango_password,
            )
            if not sys_db.has_database(settings.arango_db):
                sys_db.create_database(settings.arango_db)
            return client.db(
                settings.arango_db,
                username=settings.arango_user,
                password=settings.arango_password,
            )
        except Exception as e:  # noqa: BLE001 — connection refused while booting
            last_err = e
            time.sleep(delay)
    raise RuntimeError(
        f"Could not connect to ArangoDB at {settings.arango_url}: {last_err}"
    )
