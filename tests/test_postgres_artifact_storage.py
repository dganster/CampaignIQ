"""Opt-in PostgreSQL parity tests. Requires CAMPAIGNIQ_DEV_DATABASE_URL and PGPASSWORD."""
import os
import uuid
import pytest
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.postgres_artifact_storage import PostgresArtifactStorage


def test_postgres_artifact_storage_contract(tmp_path):
    dsn = os.getenv('CAMPAIGNIQ_DEV_DATABASE_URL')
    if not dsn:
        pytest.skip('CAMPAIGNIQ_DEV_DATABASE_URL not configured')
    namespace = 'test-' + uuid.uuid4().hex
    pg = PostgresArtifactStorage(dsn, namespace)
    pg.initialize()
    fs = LocalFilesystemArtifactStorage(tmp_path)
    try:
        for store in (fs, pg):
            assert not store.exists('2026/08/example.json')
            assert store.list_keys() == ()
            store.write_text('2026/08/example.json', '{"version": 1}\n')
            store.write_text('2026/08/other.txt', 'other\n')
            store.write_text('2026/09/example.json', '{"version": 2}\n')
            assert store.exists('2026/08/example.json')
            assert store.read_text('2026/08/example.json') == '{"version": 1}\n'
            assert store.list_keys(prefix='2026/08/', suffix='.json') == ('2026/08/example.json',)
            store.write_text('2026/08/example.json', '{"version": 3}\n')
            assert store.read_text('2026/08/example.json') == '{"version": 3}\n'
            store.delete('2026/08/example.json')
            store.delete('2026/08/example.json')
            assert not store.exists('2026/08/example.json')
            with pytest.raises(FileNotFoundError):
                store.read_text('2026/08/example.json')
            with pytest.raises(ValueError):
                store.write_text('../escape.json', 'bad')
            assert store.list_keys() == ('2026/08/other.txt', '2026/09/example.json')
        assert fs.list_keys() == pg.list_keys()
    finally:
        with pg._connect() as conn:
            conn.execute('DELETE FROM artifact_storage_dev WHERE namespace=%s', (namespace,))
