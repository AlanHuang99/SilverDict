"""Durable serial job coordinator. All DB handles belong to their calling thread."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import threading
import tempfile
import os
import uuid
import zipfile
from .catalog import Catalog, NATIVE, format_of
from .convert import convert, extract_zip, StrictLog, validate_source_bundle


def now():
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, action TEXT NOT NULL,
                group_name TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER DEFAULT 0,
                message TEXT DEFAULT '', download_url TEXT, created_at TEXT NOT NULL,
                dictionary_id TEXT, artifact TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS active_job ON jobs(source_id,action)
                WHERE status IN ('queued','running');''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='failed', message='Interrupted by restart; safe to retry', progress=0 WHERE status='running'")

    def enqueue(self, source_id, action, group):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT * FROM jobs WHERE source_id=? AND action=? AND status IN ('queued','running')", (source_id, action)).fetchone()
            if existing:
                return dict(existing)
            identity = uuid.uuid4().hex
            db.execute("INSERT INTO jobs(id,source_id,action,group_name,status,created_at) VALUES(?,?,?,?,'queued',?)", (identity, source_id, action, group, now()))
            return dict(db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone())

    def all(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY created_at DESC')]

    def get(self, identity):
        with self.connection() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
            return dict(row) if row else None

    def update(self, identity, **fields):
        allowed = {'status', 'progress', 'message', 'download_url', 'dictionary_id', 'artifact'}
        if not fields.keys() <= allowed:
            raise ValueError('Invalid job fields')
        with self.connection() as db:
            db.execute('UPDATE jobs SET ' + ','.join(f'{key}=?' for key in fields) + ' WHERE id=?', (*fields.values(), identity))

    def claim(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running',progress=5,message='Preparing source' WHERE id=?", (row['id'],))
                return dict(row)


class Library:
    def __init__(self, dictionaries, source, state, data, start=True):
        self.dictionaries = dictionaries
        self.catalog = Catalog(source)
        self.state, self.data = Path(state), Path(data)
        self.state.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.state / 'library.sqlite3')
        self.mutation_lock = threading.RLock()
        self.worker_error = None
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.lock_file = None
        if start:
            self.lock_file = (self.state / 'library-worker.lock').open('a')
            try:
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.lock_file.close()
                raise RuntimeError('Library requires exactly one application worker')
            self.store.recover()
            self.thread = threading.Thread(target=self.run, name='library-coordinator', daemon=True)
            self.thread.start()

    def groups(self):
        return [g['name'] for g in self.dictionaries.settings.get_groups()]

    def create_group(self, name):
        if not isinstance(name, str):
            raise ValueError('Group name must be text')
        name = name.strip()
        if not name or len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError('Group name must contain 1 to 80 characters without control characters')
        with self.mutation_lock:
            if name not in self.groups():
                self.dictionaries.settings.add_group({'name': name, 'lang': []})
            return self.groups()

    def reading_settings(self, group):
        if group not in self.groups():
            raise ValueError('Unknown group')
        with self.mutation_lock:
            path = self.state / 'reading-settings.json'
            saved = json.loads(path.read_text()) if path.exists() else {}
            names = self.dictionaries.settings.dictionaries_of_group(group)
            rows = [row for row in saved.get(group, []) if row['id'] in names]
            seen = {row['id'] for row in rows}
            rows += [{'id': name, 'enabled': True} for name in names if name not in seen]
            return [dict(row, title=self.dictionaries.settings.display_name_of_dictionary(row['id'])) for row in rows]

    def save_reading_settings(self, group, rows):
        with self.mutation_lock:
            current = self.reading_settings(group)
            if (not isinstance(rows, list) or any(not isinstance(row, dict) or
                    not isinstance(row.get('id'), str) or type(row.get('enabled')) is not bool for row in rows)):
                raise ValueError('Each dictionary needs an ID and an enabled flag')
            names = [row['id'] for row in rows]
            if len(names) != len(set(names)) or set(names) != {row['id'] for row in current}:
                raise ValueError('The dictionary list changed. Reload Settings and try again.')
            path = self.state / 'reading-settings.json'
            saved = json.loads(path.read_text()) if path.exists() else {}
            saved[group] = [{'id': row['id'], 'enabled': row['enabled']} for row in rows]
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=self.state, delete=False) as out:
                    temporary = Path(out.name)
                    json.dump(saved, out, ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
                temporary.replace(path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return self.reading_settings(group)

    def reading_dictionaries(self, group):
        return [row['id'] for row in self.reading_settings(group) if row['enabled']]

    def healthy(self):
        return not self.worker_error and (self.thread is None or self.thread.is_alive())

    def items(self):
        jobs = self.store.all()
        result = self.catalog.scan()
        dictionaries = self.dictionaries.settings.dictionaries_list
        registered = {d['dictionary_name'] for d in dictionaries}
        by_path = {str(Path(d['dictionary_filename']).resolve()): d['dictionary_name'] for d in dictionaries}
        for item in result:
            existing_id = by_path.get(str(self.catalog.root / item['path']))
            reserved_id = '__lib' + item['id'][:12]
            if not existing_id and reserved_id in registered:
                existing_id = reserved_id
            if existing_id:
                item.update(status='imported', dictionary_id=existing_id)
            related = [j for j in jobs if j['source_id'] == item['id']]
            imported = next((j for j in related if j['dictionary_id'] in registered), None)
            if imported:
                item.update(status='imported', dictionary_id=imported['dictionary_id'])
            if related:
                recent = related[0]
                if recent['status'] in ('queued', 'running', 'failed'):
                    item['status'] = recent['status']
                if recent['status'] == 'failed':
                    item['error'] = recent['message']
                elif recent['status'] == 'completed' and recent['action'] == 'export' and not imported and not existing_id:
                    item['status'] = 'exported'
        return result

    def enqueue(self, source_id, action, group):
        item, _ = self.catalog.get(source_id)
        if action not in ('import', 'export'):
            raise ValueError('Action must be import or export')
        if item['format'] == 'unsupported archive':
            raise ValueError('This archive format is unsupported; explicitly extract it outside the library')
        if group not in self.groups():
            raise ValueError('Unknown group')
        job = self.store.enqueue(source_id, action, group)
        self.wake.set()
        return job

    def run(self):
        try:
            while not self.stop.is_set():
                job = self.store.claim()
                if job:
                    self.execute(job)
                else:
                    self.wake.wait(2)
                    self.wake.clear()
        except Exception as exc:
            self.worker_error = str(exc)
            logging.getLogger(__name__).exception('Library coordinator stopped unexpectedly')

    def execute(self, job):
        stage = self.data / 'staging' / job['id']
        try:
            stage.mkdir(parents=True, exist_ok=True)
            warning_count = 0
            item, source = self.catalog.get(job['source_id'])
            fmt = item['format']
            if fmt == 'ZIP':
                extracted = extract_zip(source, stage / 'input')
                choices = [p for p in extracted.rglob('*') if p.is_file() and '_abrv.dsl' not in p.name.lower() and format_of(p) not in (None, 'ZIP', 'unsupported archive')]
                native_choices = [p for p in choices if format_of(p) in NATIVE]
                if native_choices:
                    choices = native_choices
                if len(choices) != 1:
                    raise ValueError('ZIP must contain exactly one dictionary plus its companion resources')
                source, fmt = choices[0], format_of(choices[0])
            if job['action'] == 'export' or fmt not in NATIVE:
                self.store.update(job['id'], progress=20, message='Converting to HTML StarDict')
                source = convert(source, fmt, stage / 'output')
                fmt = 'Stardict'
                manifest = source.parent / 'manifest.json'
                metadata = json.loads(manifest.read_text())
                warning_count = len(metadata.get('warnings', []))
                metadata.update(source=item['path'], source_id=item['id'], original_preserved=True)
                manifest.write_text(json.dumps(metadata, indent=2))
            if job['action'] == 'export':
                self.store.update(job['id'], progress=90, message='Packaging verified export')
                package = stage / 'export.zip'
                with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    for path in sorted(source.parent.rglob('*')):
                        if path.is_file() and not path.is_symlink():
                            archive.write(path, path.relative_to(source.parent))
                destination = self.data / 'exports' / (job['id'] + '.zip')
                destination.parent.mkdir(exist_ok=True)
                package.replace(destination)
                self.store.update(job['id'], artifact=str(destination), download_url=f"/api/library/jobs/{job['id']}/download", status='completed', progress=100, message='Export ready')
            else:
                with self.mutation_lock:
                    self.import_native(job, item, source, fmt, stage)
            if warning_count:
                result = self.store.get(job['id'])
                self.store.update(job['id'], message=result['message'] + f'; {warning_count} conversion warning(s), inspect manifest')
        except Exception as exc:
            self.store.update(job['id'], status='failed', message=str(exc)[-4000:], progress=0)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def import_native(self, job, item, source, fmt, stage):
        # Stable reserved reader name allows cleanup after an interrupted native index.
        name = '__lib' + item['id'][:12]
        settings = self.dictionaries.settings
        existing = next((d for d in settings.dictionaries_list if d['dictionary_name'] == name or Path(d['dictionary_filename']) == source), None)
        if existing:
            settings.add_dictionary_to_group(existing['dictionary_name'], job['group_name'])
            self.store.update(job['id'], status='completed', progress=100, dictionary_id=existing['dictionary_name'], message='Already imported')
            return
        from .. import db_manager
        db_manager.delete_dictionary(name)
        self.dictionaries._dictionaries.pop(name, None)
        # DSL readers compress beside their input. ZIP/converted inputs also need a durable home.
        if fmt == 'ABBYYLingvoDSL' or source.is_relative_to(stage):
            destination = self.data / 'imports' / item['id']
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(exist_ok=True)
            if source.is_relative_to(stage):
                shutil.copytree(source.parent, destination, symlinks=False)
            else:
                # Copy only this DSL and its conventional resources/abbreviation files.
                destination.mkdir()
                shutil.copyfile(source, destination / source.name)
                prefix = source.name.removesuffix('.dz').removesuffix('.dsl')
                for companion in source.parent.iterdir():
                    if companion.name.startswith(prefix) and companion != source:
                        if companion.is_symlink():
                            raise ValueError('Symbolic link DSL companion is not allowed')
                        if companion.is_dir():
                            for child in companion.rglob('*'):
                                if child.is_symlink():
                                    raise ValueError('Symbolic link DSL resource is not allowed')
                            shutil.copytree(companion, destination / companion.name)
                        else:
                            shutil.copyfile(companion, destination / companion.name)
            source = destination / source.name
        validate_source_bundle(source)
        self.store.update(job['id'], progress=50, message='Building native search index')
        info = {'dictionary_name': name[2:], 'dictionary_display_name': item['title'], 'dictionary_filename': str(source), 'dictionary_format': NATIVE[fmt]}
        handler = StrictLog()
        thread_id = threading.get_ident()
        handler.addFilter(lambda record: record.thread == thread_id)
        logging.getLogger().addHandler(handler)
        try:
            self.dictionaries.add_dictionary(info)
            if handler.errors:
                self.dictionaries.remove_dictionary(info)
                raise ValueError('Native import logged errors: ' + '; '.join(handler.warnings[-5:]))
            settings.add_dictionary_to_group(info['dictionary_name'], job['group_name'])
        except Exception:
            if not any(d['dictionary_name'] == name for d in settings.dictionaries_list):
                db_manager.delete_dictionary(name)
                self.dictionaries._dictionaries.pop(name, None)
            raise
        finally:
            logging.getLogger().removeHandler(handler)
        self.store.update(job['id'], status='completed', progress=100, dictionary_id=info['dictionary_name'], message='Imported')
