"""Application boundaries and HTML compatibility with synthetic dictionaries."""
import importlib
import sys
from pathlib import Path
import pytest

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('SILVERDICT_LIBRARY', '1')
    monkeypatch.setenv('SILVERDICT_STATE', str(tmp_path/'state'))
    monkeypatch.setenv('SILVERDICT_CACHE', str(tmp_path/'cache'))
    monkeypatch.setenv('SILVERDICT_PUBLIC_URL', 'https://dicts.example.test')
    sys.path.insert(0, str(Path(__file__).parents[1]/'server'))
    module=importlib.import_module('app')
    library=importlib.import_module('app.library')
    monkeypatch.setattr(module, 'Dictionaries', lambda app: None)
    def init(app):
        app.add_url_rule('/api/library/probe', view_func=lambda: {'ok': True}, methods=['GET','POST'])
    monkeypatch.setattr(library, 'init_library', init)
    return module.create_app()

def test_sandbox_cannot_change_library(app):
    client=app.test_client()
    assert client.post('/api/library/probe', json={}).status_code==403
    assert client.post('/api/library/probe', json={}, headers={'Origin':'null','X-SilverDict-Library':'1'}).status_code==403
    assert client.post('/api/library/probe', json={}, headers={'Origin':'https://evil.test','X-SilverDict-Library':'1'}).status_code==403
    assert client.post('/api/library/probe', json={}, headers={'Origin':'https://dicts.example.test','X-SilverDict-Library':'1'}).status_code==200
    assert 'Access-Control-Allow-Origin' not in client.get('/api/library/probe').headers

def test_legacy_management_and_private_cache_unavailable(app):
    client=app.test_client()
    assert client.get('/api/management/scan').status_code==404
    assert client.get('/api/cache/dict/mdx.pickle').status_code==404
    assert client.get('/api/query/Default%20Group/test').status_code==404

def test_frame_has_independent_restricted_policy(app):
    response=app.test_client().get('/library-frame')
    assert response.status_code==200
    policy=response.headers['Content-Security-Policy']
    assert 'sandbox allow-scripts;' in policy
    assert 'allow-same-origin' not in policy
    assert "connect-src https://dicts.example.test/api/cache/;" in policy
    assert "form-action 'none'" in policy
    assert 'frame.js' in response.text

def test_media_and_entry_rewrites(app):
    from app.resource_html import rewrite_article
    html=rewrite_article('<link rel="stylesheet" href="book.css"><audio src="sound/one.mp3"></audio><img src="images/a.png"><a href="entry://another word">other</a><a href="sound://two.mp3">play</a><script src="book.js"></script>', '__demo')
    assert '/api/cache/__demo/book.css' in html
    assert '/api/cache/__demo/sound/one.mp3' in html
    assert '/api/cache/__demo/images/a.png' in html
    assert '/api/cache/__demo/book.js' in html
    assert '/api/lookup/__demo/another%20word' in html
    assert '/api/cache/__demo/two.mp3' in html
    assert 'sound://' not in html

def test_star_dict_plain_file_and_alias_search(tmp_path, app):
    import struct
    from app import db_manager
    from app.settings import Settings
    from app.dicts.base_reader import BaseReader
    from app.dicts.stardict_reader import StarDictReader
    source=tmp_path/'source';source.mkdir(); cache=tmp_path/'cache';cache.mkdir(exist_ok=True)
    Settings.SQLITE_DB_FILE=str(tmp_path/'index.sqlite3')
    BaseReader._CACHE_ROOT=str(cache)
    if hasattr(db_manager.local_storage,'connection'):
        db_manager.local_storage.connection.close()
        del db_manager.local_storage.connection
    if hasattr(db_manager.local_storage,'cursor'):del db_manager.local_storage.cursor
    db_manager.init_db()
    content=b'<b>one definition</b>'
    (source/'test.ifo').write_text("StarDict's dict ifo file\nversion=2.4.2\nwordcount=1\nidxfilesize=13\nsynwordcount=1\nbookname=Test\nsametypesequence=h\n")
    (source/'test.dict').write_bytes(content)
    (source/'test.idx').write_bytes(b'word\0'+struct.pack('>II',0,len(content)))
    (source/'test.syn').write_bytes(b'alias\0'+struct.pack('>I',0))
    (source/'res').mkdir();(source/'res/book.css').write_text('body{color:red}')
    reader=StarDictReader('__demo',str(source/'test.ifo'),'Test')
    assert 'one definition' in reader.get_definition_by_key('word')
    assert 'one definition' in reader.get_definition_by_key('alias')
    assert not (cache/'__demo').is_symlink()
    assert (source/'res/book.css').read_text()=='body{color:red}'


def test_fragment_and_legacy_paths(app):
    from app.resource_html import rewrite_article
    html=rewrite_article('<a href="entry://#inside">go</a><img src="api/cache/__demo/photo.png"><a href="api/lookup/__demo/word">word</a>', '__demo')
    assert 'href="#inside"' in html
    assert 'src="/api/cache/__demo/photo.png"' in html
    assert 'href="/api/lookup/__demo/word"' in html


def test_resource_url_suffixes(app):
    from app.resource_html import rewrite_article
    html=rewrite_article('<link href="book.css?v=1"><img src="icon.svg#glyph"><a href="sound://audio/a.mp3?v=2">play</a>', '__demo')
    assert '/api/cache/__demo/book.css?v=1' in html
    assert '/api/cache/__demo/icon.svg#glyph' in html
    assert '/api/cache/__demo/audio/a.mp3?v=2' in html


def test_64bit_stardict_index(tmp_path, app):
    import struct
    from app.dicts.stardict.stardict import IdxFileReader
    path=tmp_path/'wide.idx'
    path.write_bytes(b'word\0'+struct.pack('>QI',2**32+7,11))
    assert IdxFileReader(str(path),64).get_index_by_num(0)==(b'word',2**32+7,11)


def test_settings_write_failure_preserves_previous_yaml(tmp_path, app, monkeypatch):
    from app.settings import Settings
    import app.settings as settings_module
    path=tmp_path/'settings.yaml';path.write_text('original: true\n')
    def fail(*args,**kwargs):raise OSError('disk failure')
    monkeypatch.setattr(settings_module.yaml,'dump',fail)
    with pytest.raises(OSError):Settings._save_settings_to_file(object(),{'new':True},str(path))
    assert path.read_text()=='original: true\n'
