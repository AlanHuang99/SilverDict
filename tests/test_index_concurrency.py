"""Search snapshots must not block the serialized dictionary importer."""
import importlib
from pathlib import Path
import sqlite3
import sys
import threading

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))


def test_search_snapshot_allows_import_commit(tmp_path, monkeypatch):
    module = importlib.import_module('app.db_manager')
    path = tmp_path / 'index.sqlite3'
    monkeypatch.setattr(module.Settings, 'SQLITE_DB_FILE', str(path))
    monkeypatch.setattr(module, 'local_storage', threading.local())
    module.init_db()
    conn = module.get_connection()
    assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    conn.execute('PRAGMA busy_timeout=100')
    reader = sqlite3.connect(path)
    try:
        reader.execute('BEGIN')
        assert reader.execute('SELECT count(*) FROM entries').fetchone()[0] == 0
        module.add_entry('love', '__test', 'love', 0, 10)
        module.commit_new_entries('__test')
        assert reader.execute('SELECT count(*) FROM entries').fetchone()[0] == 0
        reader.commit()
        assert reader.execute('SELECT count(*) FROM entries').fetchone()[0] == 1
    finally:
        reader.close()
        conn.close()
