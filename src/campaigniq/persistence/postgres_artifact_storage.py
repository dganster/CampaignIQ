"""Development-only PostgreSQL implementation of CampaignIQ's ArtifactStorage contract."""
from __future__ import annotations

from functools import lru_cache

from campaigniq.persistence.artifact_storage import _validate_key


@lru_cache(maxsize=8)
def _pool_for_dsn(dsn: str):
    """One bounded pool per DSN, shared by all workspace storage instances."""
    from psycopg_pool import ConnectionPool

    return ConnectionPool(conninfo=dsn, min_size=0, max_size=3, timeout=15)


@lru_cache(maxsize=8)
def _initialize_schema(dsn: str) -> None:
    """Run schema setup once per DSN per process, after successful completion."""
    with _pool_for_dsn(dsn).connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS artifact_storage_dev (
            namespace text NOT NULL,
            artifact_key text NOT NULL,
            content text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (namespace, artifact_key)
        )""")


class PostgresArtifactStorage:
    """Text artifact store isolated by namespace; does not touch source_artifacts."""

    def __init__(self, dsn: str, namespace: str = "default") -> None:
        if not namespace or len(namespace) > 128:
            raise ValueError("namespace must be 1-128 characters")
        self.dsn = dsn
        self.namespace = namespace

    def _connect(self):
        # Pool context commits on success, rolls back on failure, then returns
        # the connection for reuse; callers retain their existing with-blocks.
        return _pool_for_dsn(self.dsn).connection()

    def initialize(self) -> None:
        _initialize_schema(self.dsn)

    def exists(self, key: str) -> bool:
        _validate_key(key)
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM artifact_storage_dev WHERE namespace=%s AND artifact_key=%s", (self.namespace, key)).fetchone() is not None

    def read_text(self, key: str) -> str:
        _validate_key(key)
        with self._connect() as conn:
            row = conn.execute("SELECT content FROM artifact_storage_dev WHERE namespace=%s AND artifact_key=%s", (self.namespace, key)).fetchone()
        if row is None:
            raise FileNotFoundError(key)
        return row[0]

    def write_text(self, key: str, content: str) -> None:
        _validate_key(key)
        with self._connect() as conn:
            conn.execute("""INSERT INTO artifact_storage_dev(namespace, artifact_key, content)
                VALUES (%s,%s,%s) ON CONFLICT (namespace,artifact_key)
                DO UPDATE SET content=EXCLUDED.content, updated_at=now()""", (self.namespace, key, content))

    def delete(self, key: str) -> None:
        _validate_key(key)
        with self._connect() as conn:
            conn.execute("DELETE FROM artifact_storage_dev WHERE namespace=%s AND artifact_key=%s", (self.namespace, key))

    def list_keys(self, *, prefix: str = "", suffix: str = "") -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT artifact_key FROM artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key", (self.namespace,)).fetchall()
        return tuple(key for (key,) in rows if key.startswith(prefix) and key.endswith(suffix))
