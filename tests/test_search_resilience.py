"""A single broken dictionary must not discard other search results."""
import importlib
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))


def test_query_preserves_healthy_dictionary_and_reports_failure(monkeypatch):
    module = importlib.import_module('app.dictionaries')
    monkeypatch.setattr(module, 'stem', lambda key, langs: [key])
    monkeypatch.setattr(module.db_manager, 'entry_exists_in_dictionary', lambda key, name: True)
    class Reader:
        def __init__(self, broken=False): self.broken = broken
        def get_definitions_by_keys(self, keys):
            if self.broken: raise ValueError('invalid record block')
            return '<p>A healthy definition</p>'
    dictionaries = module.Dictionaries.__new__(module.Dictionaries)
    dictionaries.settings = SimpleNamespace(
        dictionaries_of_group=lambda group: ['good', 'broken'],
        group_lang=lambda group: [], preferences={'autoplay_audio': False},
        display_name_of_dictionary=lambda name: name.title(), add_to_history=lambda word: None)
    dictionaries._dictionaries = {'good': Reader(), 'broken': Reader(True)}
    dictionaries._transliterate_key = lambda key, langs: [key]
    dictionaries._re_legacy_lookup_api = re.compile('__never_matches__')
    failures = []
    articles = dictionaries.query('all', 'love', errors=failures)
    assert articles == [('good', 'Good', '<p>A healthy definition</p>')]
    assert failures == [{'id': 'broken', 'title': 'Broken', 'error': 'This dictionary could not read the entry.'}]
    with pytest.raises(ValueError, match='invalid record block'):
        dictionaries.query('all', 'love')  # Classic callers retain their exception behavior.


def test_library_api_errors_are_json(tmp_path, monkeypatch):
    module = importlib.import_module('app')
    library = importlib.import_module('app.library')
    monkeypatch.setenv('SILVERDICT_LIBRARY', '1')
    monkeypatch.setattr(module, 'Dictionaries', lambda app: None)
    def init(app):
        def broken(): raise ValueError('internal detail')
        app.add_url_rule('/api/library/broken', view_func=broken)
    monkeypatch.setattr(library, 'init_library', init)
    app = module.create_app()
    client = app.test_client()
    response = client.get('/api/library/broken')
    assert response.status_code == 500 and response.is_json
    assert response.json['error'] and 'internal detail' not in response.json['error']
    response = client.get('/api/library/missing')
    assert response.status_code == 404 and response.is_json
