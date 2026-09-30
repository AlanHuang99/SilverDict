"""Deferred entries must avoid unrelated readers and preserve reading preferences."""
import importlib
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parents[1] / 'server'))


@pytest.fixture
def reading(tmp_path, monkeypatch):
    module = importlib.import_module('app.dictionaries')
    library_module = importlib.import_module('app.library')
    monkeypatch.setattr(module, 'stem', lambda key, langs: ['love'] if key == 'loves' else [key])
    monkeypatch.setattr(module.db_manager, 'entry_exists_in_dictionary', lambda key, name: key == 'love')
    class Reader:
        def __init__(self, name): self.name = name
        def get_definitions_by_keys(self, keys):
            if self.name == 'broken': raise ValueError('invalid record block')
            return '<p>' + self.name + ' definition</p>' if keys else ''
    dictionaries = module.Dictionaries.__new__(module.Dictionaries)
    dictionaries.settings = SimpleNamespace(
        dictionaries_of_group=lambda group: ['first', 'second', 'broken'] if group == 'Default Group' else ['second'],
        get_groups=lambda: [{'name': 'Default Group'}, {'name': 'Other'}],
        group_lang=lambda group: [], preferences={'autoplay_audio': False},
        display_name_of_dictionary=lambda name: name.title(), add_to_history=lambda word: None)
    dictionaries._dictionaries = {name: Reader(name) for name in ['first', 'second', 'broken']}
    dictionaries._transliterate_key = lambda key, langs: [key]
    dictionaries._re_legacy_lookup_api = re.compile('__never_matches__')
    dictionaries.suggestions = lambda *args, **kwargs: []
    for name, value in [('SOURCE', 'source'), ('STATE', 'state'), ('LIBRARY_DATA', 'data')]:
        monkeypatch.setenv('SILVERDICT_' + name, str(tmp_path / value))
    (tmp_path / 'source').mkdir()
    app = Flask(__name__)
    app.config['LIBRARY_DISABLE_WORKER'] = True
    app.extensions['dictionaries'] = dictionaries
    library = library_module.init_library(app)
    return app.test_client(), library, dictionaries


def test_match_list_does_not_decode_broken_dictionary_and_preserves_stems(reading):
    client, _, _ = reading
    response = client.get('/api/library/search?q=loves&deferred=1')
    assert response.status_code == 200
    assert response.json['articles'] == [
        {'id': 'first', 'title': 'First'}, {'id': 'second', 'title': 'Second'}, {'id': 'broken', 'title': 'Broken'}]
    assert response.json['warnings'] == []


def test_entry_only_reads_requested_dictionary_and_rejects_outside_group(reading):
    client, _, _ = reading
    response = client.get('/api/library/entry?q=loves&id=second')
    assert response.status_code == 200
    assert 'second definition' in response.json['html']
    assert client.get('/api/library/entry?q=love&id=first&group=Other').status_code == 404
    assert client.get('/api/library/entry?q=missing&id=first').status_code == 404
    response = client.get('/api/library/entry?q=love&id=broken')
    assert response.status_code == 422 and response.is_json
    assert 'invalid record block' not in response.json['error']


def test_selection_and_order_persist_without_affecting_other_groups(reading):
    client, library, dictionaries = reading
    rows = [{'id': 'second', 'enabled': True}, {'id': 'first', 'enabled': False}, {'id': 'broken', 'enabled': False}]
    response = client.put('/api/library/reading-settings', json={'group': 'Default Group', 'dictionaries': rows})
    assert response.status_code == 200
    response = client.get('/api/library/search?q=love&deferred=1')
    assert response.json['articles'] == [{'id': 'second', 'title': 'Second'}]
    assert client.get('/api/library/entry?q=love&id=first').status_code == 404
    assert client.get('/api/library/search?q=love').json['articles'][0]['id'] == 'second'
    assert client.get('/api/library/reading-settings?group=Other').json['dictionaries'] == [
        {'id': 'second', 'title': 'Second', 'enabled': True}]
    # Reload through a new service instance to verify durable storage.
    from app.library.jobs import Library
    restarted = Library(dictionaries, library.catalog.root, library.state, library.data, start=False)
    assert [row['id'] for row in restarted.reading_settings('Default Group') if row['enabled']] == ['second']
    rows[1]['enabled'] = True
    assert client.put('/api/library/reading-settings', json={'group': 'Default Group', 'dictionaries': rows}).status_code == 200
    assert [a['id'] for a in client.get('/api/library/search?q=love&deferred=1').json['articles']] == ['second', 'first']


@pytest.mark.parametrize('rows', [None, [], [{'id': 'first', 'enabled': True}]*3,
    [{'id': 'first', 'enabled': 'false'}, {'id': 'second', 'enabled': True}, {'id': 'broken', 'enabled': False}],
    [{'id': 'outside', 'enabled': True}, {'id': 'second', 'enabled': True}, {'id': 'broken', 'enabled': False}]])
def test_invalid_selection_does_not_overwrite_preferences(reading, rows):
    client, _, _ = reading
    before = client.get('/api/library/reading-settings').json
    assert client.put('/api/library/reading-settings', json={'dictionaries': rows}).status_code == 400
    assert client.get('/api/library/reading-settings').json == before


def test_empty_selection_returns_no_results(reading):
    client, _, _ = reading
    rows = [{'id': name, 'enabled': False} for name in ['first', 'second', 'broken']]
    assert client.put('/api/library/reading-settings', json={'dictionaries': rows}).status_code == 200
    assert client.get('/api/library/search?q=love&deferred=1').json['articles'] == []


def test_new_group_member_is_enabled_without_losing_saved_order(reading):
    client, library, dictionaries = reading
    rows = [{'id': 'second', 'enabled': True}, {'id': 'first', 'enabled': False}, {'id': 'broken', 'enabled': False}]
    assert client.put('/api/library/reading-settings', json={'dictionaries': rows}).status_code == 200
    dictionaries.settings.dictionaries_of_group = lambda group: ['first', 'second', 'broken', 'new']
    result = library.reading_settings('Default Group')
    assert [(row['id'], row['enabled']) for row in result] == [('second', True), ('first', False), ('broken', False), ('new', True)]
    # A stale Settings tab must not silently drop a newly imported dictionary.
    assert client.put('/api/library/reading-settings', json={'dictionaries': rows}).status_code == 400


def test_disabled_dictionaries_do_not_supply_suggestions(reading, monkeypatch):
    _, _, dictionaries = reading
    module = importlib.import_module('app.dictionaries')
    del dictionaries.suggestions
    dictionaries.settings.WILDCARDS = {'^': '%'}
    dictionaries.settings.misc_configs = {'num_suggestions': 5}
    # The index fixture contains distinct suggestions in the two dictionaries.
    monkeypatch.setattr(module.db_manager, 'select_entries_like', lambda key, names, limit:
        ['first word' if name == 'first' else 'second word' for name in names])
    assert dictionaries.suggestions('Default Group', '^', dictionary_names=['second']) == ['second word']
    assert dictionaries.suggestions('Default Group', '^', dictionary_names=[]) == []
