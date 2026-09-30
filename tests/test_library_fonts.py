"""Shared web fonts remain confined to a dedicated, optional font directory."""
import importlib
from pathlib import Path
import sys
import pytest
from flask import Flask


@pytest.fixture
def font_app(tmp_path, monkeypatch):
    monkeypatch.setenv('SILVERDICT_LIBRARY', '1')
    monkeypatch.setenv('SILVERDICT_STATE', str(tmp_path / 'state'))
    monkeypatch.setenv('SILVERDICT_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setenv('SILVERDICT_PUBLIC_URL', 'https://dicts.example.test')
    monkeypatch.setenv('SILVERDICT_FONTS', str(tmp_path / 'fonts'))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'server'))
    module = importlib.import_module('app')
    library = importlib.import_module('app.library')
    monkeypatch.setattr(module, 'Dictionaries', lambda app: None)
    monkeypatch.setattr(library, 'init_library', lambda app: None)
    return module.create_app(), tmp_path / 'fonts'


def test_missing_shared_stylesheet_is_safe_empty(font_app):
    app, fonts = font_app
    response = app.test_client().get('/library-fonts/fonts.css')
    assert response.status_code == 200
    assert response.data == b''
    assert response.mimetype == 'text/css'
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    assert app.test_client().get('/library-fonts/missing.ttf').status_code == 404


@pytest.mark.parametrize('extension,mime', [('woff', 'font/woff'), ('woff2', 'font/woff2'), ('ttf', 'font/ttf'), ('otf', 'font/otf')])
def test_font_bytes_mime_and_opaque_origin_cors(font_app, extension, mime):
    app, fonts = font_app
    fonts.mkdir()
    (fonts / ('Example.' + extension)).write_bytes(b'synthetic font bytes')
    response = app.test_client().get('/library-fonts/Example.' + extension, headers={'Origin': 'null'})
    assert response.status_code == 200
    assert response.data == b'synthetic font bytes'
    assert response.mimetype == mime
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    assert 'Access-Control-Allow-Credentials' not in response.headers
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_fonts_reject_traversal_links_and_nonfont_files(font_app, tmp_path):
    app, fonts = font_app
    fonts.mkdir()
    secret = tmp_path / 'private.ttf'
    secret.write_bytes(b'private')
    (fonts / 'escape.ttf').symlink_to(secret)
    (fonts / 'fonts.css').symlink_to(secret)
    (fonts / 'notes.json').write_bytes(b'private notes')
    (fonts / 'other.css').write_bytes(b'not allowed')
    (fonts / 'linked').symlink_to(tmp_path, target_is_directory=True)
    for name in ('../private.ttf', '%2e%2e/private.ttf', '..%5cprivate.ttf', 'escape.ttf', 'fonts.css', 'notes.json', 'other.css', 'linked/private.ttf'):
        assert app.test_client().get('/library-fonts/' + name).status_code == 404, name


def test_frame_csp_allows_only_shared_css_and_fonts(font_app):
    app, fonts = font_app
    policy = app.test_client().get('/library-frame').headers['Content-Security-Policy']
    directives = {part.strip().split()[0]: part.strip().split()[1:] for part in policy.split(';') if part.strip()}
    assert 'https://dicts.example.test/library-fonts/' in directives['font-src']
    assert 'https://dicts.example.test/library-fonts/fonts.css' in directives['style-src']
    assert all('/library-fonts/' not in entry for entry in directives['script-src'])
    assert directives['connect-src'] == ['https://dicts.example.test/api/cache/']
    assert 'allow-same-origin' not in directives['sandbox']


def test_font_routes_disabled_outside_library_mode(monkeypatch):
    monkeypatch.delenv('SILVERDICT_LIBRARY', raising=False)
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'server'))
    module = importlib.import_module('app.library_fonts')
    app = Flask(__name__)
    module.init_library_fonts(app)
    assert app.test_client().get('/library-fonts/fonts.css').status_code == 404
