"""Synthetic fixtures only; no personal source data."""
import importlib.util
import json
import logging
from pathlib import Path
import stat
import struct
import sys
import types
import zipfile
import pytest
from flask import Flask

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / 'server/app/library'
spec = importlib.util.spec_from_file_location('library_under_test', PACKAGE / '__init__.py', submodule_search_locations=[str(PACKAGE)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from library_under_test.catalog import Catalog, confined
from library_under_test.jobs import JobStore, Library
from library_under_test.convert import extract_zip, copy_assets, validate_stardict, StrictLog, convert


def test_confined_and_distinct_ids(tmp_path):
    for folder in ('a', 'b'):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / 'same.tsv').write_text('a\tb')
    (tmp_path / 'escape.tsv').symlink_to('/etc/passwd')
    (tmp_path / 'linked').symlink_to(tmp_path / 'a', target_is_directory=True)
    items = Catalog(tmp_path).scan()
    assert len(items) == 2 and len({item['id'] for item in items}) == 2
    for path in ('../escape', '/etc/passwd', 'escape.tsv', 'linked/same.tsv'):
        with pytest.raises((ValueError, FileNotFoundError)):
            confined(tmp_path, path)


def test_dedup_and_restart_recovery(tmp_path):
    store = JobStore(tmp_path / 'jobs.sqlite3')
    first = store.enqueue('abc', 'import', 'Default Group')
    assert store.enqueue('abc', 'import', 'Default Group')['id'] == first['id']
    assert store.claim()['id'] == first['id']
    assert store.claim() is None
    recovered = JobStore(tmp_path / 'jobs.sqlite3')
    recovered.recover()
    assert recovered.get(first['id'])['status'] == 'failed'
    assert recovered.enqueue('abc', 'import', 'Default Group')['id'] != first['id']


@pytest.mark.parametrize('name,mode', [('../escape', 0), ('/escape', 0), ('C:/escape', 0), ('..\\escape', 0), ('link', stat.S_IFLNK | 0o777)])
def test_unsafe_archive(tmp_path, name, mode):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16
        out.writestr(info, 'target')
    with pytest.raises(ValueError):
        extract_zip(archive, tmp_path / 'out')


def test_archive_size_and_duplicate_limits(tmp_path, monkeypatch):
    import library_under_test.convert as converter
    monkeypatch.setattr(converter, 'MAX_ARCHIVE_BYTES', 3)
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('large.tsv', '12345')
    with pytest.raises(ValueError):
        extract_zip(archive, tmp_path / 'out')


def test_nested_assets_and_originals(tmp_path):
    source = tmp_path / 'source'
    (source / 'fonts').mkdir(parents=True)
    (source / 'fonts/font.woff2').write_bytes(b'font')
    (source / 'style.css').write_text('body{}')
    (source / 'other.mdx').write_bytes(b'not copied')
    destination = tmp_path / 'res'
    copy_assets(source, destination)
    assert (destination / 'fonts/font.woff2').read_bytes() == b'font'
    assert not (destination / 'other.mdx').exists()
    (destination / 'style.css').write_text('changed')
    assert (source / 'style.css').read_text() == 'body{}'


def stardict(tmp_path):
    (tmp_path / 'dictionary.ifo').write_text("StarDict's dict ifo file\nwordcount=1\nsametypesequence=h\nsynwordcount=1\n")
    (tmp_path / 'dictionary.dict').write_bytes(b'hello')
    (tmp_path / 'dictionary.idx').write_bytes(b'word\0' + struct.pack('>II', 0, 5))
    (tmp_path / 'dictionary.syn').write_bytes(b'alias\0' + struct.pack('>I', 0))
    return tmp_path / 'dictionary.ifo'


def test_alias_validation(tmp_path):
    ifo = stardict(tmp_path)
    assert validate_stardict(ifo) == {'entries': 1, 'aliases': 1}
    (tmp_path / 'dictionary.syn').write_bytes(b'alias\0' + struct.pack('>I', 1))
    with pytest.raises(ValueError, match='Alias'):
        validate_stardict(ifo)


def test_logged_error_is_counted():
    handler = StrictLog()
    handler.emit(logging.LogRecord('pyglossary', logging.ERROR, '', 0, 'Resource failed', (), None))
    assert handler.errors == 1 and handler.warnings == ['Resource failed']


class Settings:
    dictionaries_list = []
    def get_groups(self):
        return [{'name': 'Default Group'}]
    def dictionaries_of_group(self, group):
        return []


def service(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'tiny.tsv').write_text('alpha\t<b>first</b>\nbeta\tsecond\n')
    return Library(types.SimpleNamespace(settings=Settings()), source, tmp_path / 'state', tmp_path / 'data', start=False)


def test_conversion_failure_not_published(tmp_path, monkeypatch):
    library = service(tmp_path)
    identity = library.catalog.scan()[0]['id']
    job = library.enqueue(identity, 'export', 'Default Group')
    def fail(*args):
        raise ValueError('Resource error')
    monkeypatch.setattr('library_under_test.jobs.convert', fail)
    library.execute(job)
    result = library.store.get(job['id'])
    assert result['status'] == 'failed'
    assert result['download_url'] is None
    assert not (library.data / 'exports').exists()


def test_real_tsv_subprocess_export(tmp_path):
    pytest.importorskip('pyglossary')
    library = service(tmp_path)
    identity = library.catalog.scan()[0]['id']
    job = library.enqueue(identity, 'export', 'Default Group')
    library.execute(job)
    result = library.store.get(job['id'])
    assert result['status'] == 'completed', result['message']
    with zipfile.ZipFile(result['artifact']) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['entries'] == 2 and manifest['original_preserved']
        assert 'dictionary.ifo' in archive.namelist()
    assert (library.catalog.root / 'tiny.tsv').read_text().startswith('alpha')


def test_api_contract_empty_search_and_download(tmp_path, monkeypatch):
    library = service(tmp_path)
    monkeypatch.setenv('SILVERDICT_SOURCE', str(library.catalog.root))
    monkeypatch.setenv('SILVERDICT_STATE', str(library.state))
    monkeypatch.setenv('SILVERDICT_LIBRARY_DATA', str(library.data))
    app = Flask(__name__)
    app.config['LIBRARY_DISABLE_WORKER'] = True
    app.extensions['dictionaries'] = library.dictionaries
    module.init_library(app)
    client = app.test_client()
    assert client.get('/api/library/health').json == {'status': 'ok'}
    assert client.get('/api/library/search?q=hello').json == {'articles': [], 'suggestions': []}
    assert client.get('/api/library/search?group=wrong').status_code == 400
    item = client.get('/api/library/catalog').json['items'][0]
    result = client.post('/api/library/jobs', json={'source_id': item['id'], 'action': 'export'})
    assert result.status_code == 202
    assert client.get('/api/library/jobs').json['jobs'][0]['id'] == result.json['id']
    assert client.get('/api/library/jobs/' + result.json['id'] + '/download').status_code == 404
    assert client.post('/api/library/jobs', json=[]).status_code == 400


def test_concurrent_queue_deduplicates(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = JobStore(tmp_path / 'jobs.sqlite3')
    with ThreadPoolExecutor(max_workers=8) as workers:
        jobs = list(workers.map(lambda _: store.enqueue('same', 'export', 'Default Group'), range(16)))
    assert len({job['id'] for job in jobs}) == 1


def test_export_download_is_browser_usable(tmp_path, monkeypatch):
    library = service(tmp_path)
    monkeypatch.setenv('SILVERDICT_SOURCE', str(library.catalog.root))
    monkeypatch.setenv('SILVERDICT_STATE', str(library.state))
    monkeypatch.setenv('SILVERDICT_LIBRARY_DATA', str(library.data))
    app = Flask(__name__)
    app.config['LIBRARY_DISABLE_WORKER'] = True
    app.extensions['dictionaries'] = library.dictionaries
    active = module.init_library(app)
    job = active.enqueue(active.catalog.scan()[0]['id'], 'export', 'Default Group')
    active.execute(job)
    job = active.store.get(job['id'])
    response = app.test_client().get(job['download_url'])
    assert response.status_code == 200
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert response.data.startswith(b'PK')
    response.close()


def test_broken_resource_symlink_rejected(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'bad.css').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='Symbolic link'):
        copy_assets(source, tmp_path / 'output')


def test_existing_native_registration_visible(tmp_path):
    library = service(tmp_path)
    library.dictionaries.settings = Settings()
    library.dictionaries.settings.dictionaries_list = [{'dictionary_name': '__old', 'dictionary_filename': str(library.catalog.root / 'tiny.tsv')}]
    assert library.items()[0]['dictionary_id'] == '__old'
    assert library.items()[0]['status'] == 'imported'


@pytest.mark.parametrize('target', ['missing', 'alias'])
def test_mdx_missing_and_chained_aliases_rejected(tmp_path, monkeypatch, target):
    pytest.importorskip('pyglossary')
    from library_under_test.convert import validate_mdx_aliases
    import pyglossary.plugin_lib.readmdict as mdict
    class FakeMDX:
        def __init__(self, path):
            pass
        def items(self):
            return [(b'word', b'definition'), (b'alias', b'@@@LINK=word'), (b'bad', ('@@@LINK=' + target).encode())]
    monkeypatch.setattr(mdict, 'MDX', FakeMDX)
    with pytest.raises(ValueError, match='alias'):
        validate_mdx_aliases('synthetic.mdx', tmp_path / 'aliases.sqlite3')


def test_mdx_direct_aliases_pass(tmp_path, monkeypatch):
    pytest.importorskip('pyglossary')
    from library_under_test.convert import validate_mdx_aliases
    import pyglossary.plugin_lib.readmdict as mdict
    class FakeMDX:
        def __init__(self, path):
            pass
        def items(self):
            return [(b'word', b'definition'), (b'alias', b'@@@LINK=word')]
    monkeypatch.setattr(mdict, 'MDX', FakeMDX)
    validate_mdx_aliases('synthetic.mdx', tmp_path / 'aliases.sqlite3')


def test_real_html_assets_aliases_and_missing_warning(tmp_path):
    pytest.importorskip('pyglossary')
    library = service(tmp_path)
    source = library.catalog.root
    (source / 'fonts').mkdir()
    (source / 'fonts/font.woff2').write_bytes(b'font')
    (source / 'picture.png').write_bytes(b'image')
    (source / 'voice.mp3').write_bytes(b'audio')
    (source / 'style.css').write_text('@font-face{src:url("fonts/font.woff2")}body{background:url("missing-background.png")}')
    (source / 'tiny.tsv').write_text('alpha|alias\t<link rel="stylesheet" href="style.css"><img src="picture.png"><audio src="voice.mp3"></audio><img src="missing.png">\n')
    job = library.enqueue(library.catalog.scan()[0]['id'], 'export', 'Default Group')
    library.execute(job)
    result = library.store.get(job['id'])
    assert result['status'] == 'completed', result['message']
    assert 'warning' in result['message']
    with zipfile.ZipFile(result['artifact']) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['aliases'] == 1
        assert manifest['resource_audit']['missing_references'] == 2
        assert manifest['resource_audit']['audit_complete']
        assert manifest['resource_audit']['checked_references'] == 6
        assert {'res/style.css', 'res/fonts/font.woff2', 'res/picture.png', 'res/voice.mp3'} <= set(archive.namelist())
        assert archive.read('dictionary.syn').startswith(b'alias\0')


def test_group_creation_validation_idempotent_and_health(tmp_path, monkeypatch):
    library = service(tmp_path)
    class GroupSettings(Settings):
        def __init__(self):
            self.groups = [{'name': 'Default Group'}]
        def get_groups(self):
            return self.groups
        def add_group(self, group):
            self.groups.append(group)
    library.dictionaries.settings = GroupSettings()
    monkeypatch.setenv('SILVERDICT_SOURCE', str(library.catalog.root))
    monkeypatch.setenv('SILVERDICT_STATE', str(library.state))
    monkeypatch.setenv('SILVERDICT_LIBRARY_DATA', str(library.data))
    app = Flask(__name__)
    app.config['LIBRARY_DISABLE_WORKER'] = True
    app.extensions['dictionaries'] = library.dictionaries
    active = module.init_library(app)
    client = app.test_client()
    assert client.post('/api/library/groups', json={'name': ' Medical '}).json['groups'] == ['Default Group', 'Medical']
    assert client.post('/api/library/groups', json={'name': 'Medical'}).json['groups'] == ['Default Group', 'Medical']
    for name in ('', 'a' * 81, 'bad\x00name', [], None):
        assert client.post('/api/library/groups', json={'name': name}).status_code == 400
    assert client.post('/api/library/jobs', json={'source_id': active.catalog.scan()[0]['id'], 'action': 'export', 'group': 'Medical'}).status_code == 202
    active.worker_error = 'test failure'
    assert client.get('/api/library/health').status_code == 503


def test_stage_failure_marks_job_failed(tmp_path):
    library = service(tmp_path)
    (library.data / 'staging').write_bytes(b'not a directory')
    job = library.enqueue(library.catalog.scan()[0]['id'], 'export', 'Default Group')
    library.execute(job)
    assert library.store.get(job['id'])['status'] == 'failed'


def test_zip_dsl_abbreviations_are_companions(tmp_path, monkeypatch):
    library = service(tmp_path)
    (library.catalog.root / 'tiny.tsv').unlink()
    with zipfile.ZipFile(library.catalog.root / 'book.zip', 'w') as archive:
        archive.writestr('book.dsl', '#NAME "Book"\nword\n definition\n')
        archive.writestr('book_abrv.dsl', '#NAME "Abbreviations"\na\n abbreviation\n')
    imported = []
    def native(job, item, source, fmt, stage):
        imported.append((source.name, fmt))
        assert (source.parent / 'book_abrv.dsl').exists()
        library.store.update(job['id'], status='completed')
    monkeypatch.setattr(library, 'import_native', native)
    job = library.enqueue(library.catalog.scan()[0]['id'], 'import', 'Default Group')
    library.execute(job)
    assert library.store.get(job['id'])['status'] == 'completed'
    assert imported == [('book.dsl', 'ABBYYLingvoDSL')]


def test_decoded_resource_save_limits_and_no_oversized_write(tmp_path):
    pytest.importorskip('pyglossary')
    import library_under_test.convert as converter
    from pyglossary.entry import DataEntry
    one, two = DataEntry('one.bin', b'123'), DataEntry('two.bin', b'456')
    with converter.resource_guards(DataEntry, max_bytes=5, max_files=10):
        one.save(str(tmp_path))
        with pytest.raises(ValueError, match='limit'):
            two.save(str(tmp_path))
    assert (tmp_path / 'one.bin').read_bytes() == b'123'
    assert not (tmp_path / 'two.bin').exists()
    with converter.resource_guards(DataEntry, max_bytes=100, max_files=1):
        DataEntry('three.bin', b'3').save(str(tmp_path))
        with pytest.raises(ValueError, match='limit'):
            DataEntry('four.bin', b'4').save(str(tmp_path))
    assert not (tmp_path / 'four.bin').exists()


def test_decoded_resource_limit_before_converter_temporary_write(tmp_path):
    pytest.importorskip('pyglossary')
    import library_under_test.convert as converter
    from pyglossary.entry import DataEntry
    with converter.resource_guards(DataEntry, max_bytes=2, max_files=10):
        with pytest.raises(ValueError, match='limit'):
            DataEntry('large.bin', b'123', tmpPath=str(tmp_path / 'temporary.bin'))
    assert not (tmp_path / 'temporary.bin').exists()


def test_decoded_resource_failure_never_publishes_export(tmp_path, monkeypatch):
    pytest.importorskip('pyglossary')
    import library_under_test.convert as converter
    from pyglossary.entry import DataEntry
    library = service(tmp_path)
    def oversized(source, fmt, output):
        output.mkdir(parents=True)
        with converter.resource_guards(DataEntry, max_bytes=3, max_files=10):
            DataEntry('one.bin', b'12').save(str(output))
            DataEntry('two.bin', b'34').save(str(output))
    monkeypatch.setattr('library_under_test.jobs.convert', oversized)
    job = library.enqueue(library.catalog.scan()[0]['id'], 'export', 'Default Group')
    library.execute(job)
    result = library.store.get(job['id'])
    assert result['status'] == 'failed' and 'limit' in result['message']
    assert result['download_url'] is None
    assert not (library.data / 'exports').exists()
    assert not (library.data / 'staging' / job['id']).exists()


def test_decoded_and_sidecar_resources_share_output_budget(tmp_path):
    pytest.importorskip('pyglossary')
    import library_under_test.convert as converter
    from pyglossary.entry import DataEntry
    source, output = tmp_path / 'source', tmp_path / 'output'
    source.mkdir()
    output.mkdir()
    (source / 'font.woff').write_bytes(b'123')
    with converter.resource_guards(DataEntry, max_bytes=5, max_files=10) as budget:
        DataEntry('image.png', b'456').save(str(output))
        with pytest.raises(ValueError, match='limit'):
            converter.copy_assets(source, output, budget=budget)
    assert not (output / 'font.woff').exists()


def test_resource_temp_and_final_saves_have_separate_budgets(tmp_path):
    pytest.importorskip('pyglossary')
    import library_under_test.convert as converter
    from pyglossary.entry import DataEntry
    temporary, output = tmp_path / 'cache', tmp_path / 'res'
    temporary.mkdir()
    output.mkdir()
    first = DataEntry('one.png', b'123')
    over = DataEntry('over.png', b'456')
    with converter.resource_guards(DataEntry, max_bytes=5, max_files=10, output_directory=output) as final_budget:
        first.save(str(temporary))
        first.save(str(output))
        assert final_budget.bytes == 3
        assert final_budget.files == 1
        with pytest.raises(ValueError, match='limit'):
            over.save(str(temporary))
        with pytest.raises(ValueError, match='limit'):
            over.save(str(output))
        final_budget.reserve(2)
        assert final_budget.bytes == 5
    assert (output / 'one.png').read_bytes() == b'123'
    assert not (output / 'over.png').exists()
    assert not (temporary / 'over.png').exists()


def test_css_audit_ignores_comments_but_keeps_active_references(tmp_path):
    from library_under_test.convert import ResourceAudit
    audit = ResourceAudit(tmp_path)
    audit.css('/* @font-face {src:url(Palatino.ttf)} @import "DFKai-SB.css"; */ body {background:url("active.png")}')
    assert audit.result()['checked_references'] == 1
    assert audit.result()['missing_samples'] == ['active.png']


def test_css_audit_preserves_comment_like_text_inside_quoted_url(tmp_path):
    from library_under_test.convert import ResourceAudit
    asset = tmp_path / 'res/font/*literal*/actual.woff'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'font')
    audit = ResourceAudit(tmp_path)
    audit.css('@font-face {src:url("font/*literal*/actual.woff")} /* url(ignored.ttf) */')
    assert audit.result()['checked_references'] == 1
    assert audit.result()['missing_references'] == 0


def test_css_comment_state_survives_streaming_chunks(tmp_path):
    from library_under_test.convert import audit_resources
    ifo = stardict(tmp_path)
    (tmp_path / 'large.css').write_text('/*' + 'x' * (1024 * 1024) + '\nurl(ignored.woff)\n*/\nbody{background:url(active.png)}')
    audit = audit_resources(ifo)
    assert audit['checked_references'] == 1
    assert audit['missing_samples'] == ['active.png']
