"""Exercise random-access MDX decoding without initializing or mutating reader state."""
import ast
import io
from pathlib import Path
import struct
import types
import zlib
import pytest

source = Path(__file__).parents[1] / 'server/app/dicts/mdict_reader.py'
node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'MDictReader')
namespace = {'BaseReader': object, 'struct': struct, 'zlib': zlib, 'os': __import__('os'), 'bisect': __import__('bisect')}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
Reader = namespace['MDictReader']


def fixture(blocks, declared_extra=0):
    compressed = [b'\x02\0\0\0' + struct.pack('>I', zlib.adler32(block) & 0xffffffff) + zlib.compress(block) for block in blocks]
    data = struct.pack('>QQQQ', len(blocks), 1, len(blocks) * 16, sum(map(len, compressed)) + declared_extra)
    data += b''.join(struct.pack('>QQ', len(encoded), len(raw)) for encoded, raw in zip(compressed, blocks))
    data += b''.join(compressed)
    reader = Reader.__new__(Reader)
    reader._mdict = types.SimpleNamespace(_version=2, _record_block_offset=0, _num_entries=1, _number_width=8, _encoding='utf-8', _read_number=lambda stream: struct.unpack('>Q', stream.read(8))[0])
    return reader, io.BytesIO(data)


def test_truncated_source_reports_corruption_instead_of_unpack_error():
    reader, stream = fixture([b'first', b'second'])
    truncated = io.BytesIO(stream.getvalue()[:-12])
    with pytest.raises(ValueError, match='[Tt]runcated'):
        reader._get_record(truncated, 5, 6)


def test_record_spans_compressed_blocks_and_utf8_boundary():
    record = 'word 猫 definition'.encode()
    boundary = record.index('猫'.encode()) + 1
    reader, stream = fixture([record[:boundary], record[boundary:]])
    assert reader._get_record(stream, 0, len(record)) == 'word 猫 definition'


def test_record_exact_block_boundary_and_last_record():
    reader, stream = fixture([b'first', b'second'])
    assert reader._get_record(stream, 5, 6) == 'second'
    assert reader._get_record(stream, 0, -1) == 'firstsecond'


def test_out_of_range_offset_is_explicit():
    reader, stream = fixture([b'first'])
    with pytest.raises(ValueError, match='offset|range'):
        reader._get_record(stream, 500, 3)


def test_corrupt_record_metadata_is_explicit():
    reader, stream = fixture([b'first'], declared_extra=10)
    with pytest.raises(ValueError):
        reader._get_record(stream, 0, 5)


def test_checksum_failure_is_explicit():
    reader, stream = fixture([b'first'])
    data = bytearray(stream.getvalue())
    data[32 + 16 + 4] ^= 1
    with pytest.raises(ValueError, match='checksum'):
        reader._get_record(io.BytesIO(data), 0, 5)


def test_v1_uncompressed_record_and_invalid_compression():
    raw = b'plain definition'
    block = b'\0\0\0\0' + struct.pack('>I', zlib.adler32(raw) & 0xffffffff) + raw
    data = struct.pack('>IIIIII', 1, 1, 8, len(block), len(block), len(raw)) + block
    reader = Reader.__new__(Reader)
    reader._mdict = types.SimpleNamespace(_version=1.2, _record_block_offset=0, _num_entries=1, _number_width=4, _encoding='utf-8', _read_number=lambda stream: struct.unpack('>I', stream.read(4))[0])
    assert reader._get_record(io.BytesIO(data), 0, len(raw)) == 'plain definition'
    unsupported = bytearray(data)
    unsupported[24] = 7
    with pytest.raises(ValueError, match='compression'):
        reader._get_record(io.BytesIO(unsupported), 0, len(raw))


def test_lookup_error_closes_source_file(tmp_path, monkeypatch):
    reader, stream = fixture([b'first'])
    path = tmp_path / 'truncated.mdx'
    path.write_bytes(stream.getvalue()[:-5])
    reader.filename = str(path)
    reader._loaded_content_into_memory = False
    handles = []
    original_open = open
    def tracked_open(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        handles.append(handle)
        return handle
    monkeypatch.setitem(namespace, 'open', tracked_open)
    with pytest.raises(ValueError, match='Truncated'):
        reader._get_records_in_batch([(0, 5)])
    assert len(handles) == 1 and handles[0].closed


def test_retry_reparses_cached_mdx_without_key_list(tmp_path, monkeypatch):
    import pickle
    monkeypatch.delenv('SILVERDICT_LIBRARY', raising=False)
    dictionary = tmp_path / 'source.mdx'
    dictionary.write_bytes(b'synthetic source')
    cache = tmp_path / 'cache'
    resources = cache / 'test'
    resources.mkdir(parents=True)
    cached = types.SimpleNamespace(_fname=str(dictionary), header={})
    with (resources / 'mdx.pickle').open('wb') as stream:
        pickle.dump(cached, stream)
    parsed = []
    entries = []
    class FakeBaseReader:
        _CACHE_ROOT = str(cache)
        def __init__(self, name, filename, display_name):
            self.name, self.filename = name, filename
        @staticmethod
        def simplify(word):
            return word.lower()
    def parse_mdx(filename):
        parsed.append(filename)
        return types.SimpleNamespace(_fname=filename, header={}, _key_list=[(0, b'Word'), (4, b'Next')])
    local = dict(namespace, BaseReader=FakeBaseReader, Path=Path, pickle=pickle,
                 MDX=parse_mdx, HTMLCleaner=lambda *args: None,
                 logger=types.SimpleNamespace(info=lambda *args: None),
                 db_manager=types.SimpleNamespace(dictionary_exists=lambda name: False,
                     drop_index=lambda: None, add_entry=lambda *args: entries.append(args),
                     commit_new_entries=lambda name: None, create_index=lambda: None))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), local)
    reader = local['MDictReader']('test', str(dictionary), 'Test', extract_resources=False)
    assert parsed == [str(dictionary)]
    assert entries == [('word', 'test', 'Word', 0, 4), ('next', 'test', 'Next', 4, -1)]
    assert not hasattr(reader._mdict, '_key_list')
    with (resources / 'mdx.pickle').open('rb') as stream:
        assert not hasattr(pickle.load(stream), '_key_list')
    assert dictionary.read_bytes() == b'synthetic source'


def test_mdd_finder_metadata_is_ignored_without_skipping_media(tmp_path, monkeypatch):
    reader = Reader.__new__(Reader)
    reader._resources_dir = str(tmp_path)
    monkeypatch.setitem(namespace, 'Path', Path)
    monkeypatch.setitem(namespace, 'resource_path', lambda root, name: Path(root) / name)
    reader._write_to_cache_dir('audio/.DS_Store', b'Finder metadata')
    reader._write_to_cache_dir('audio/love.mp3', b'pronunciation')
    assert not (tmp_path / 'audio/.DS_Store').exists()
    assert (tmp_path / 'audio/love.mp3').read_bytes() == b'pronunciation'
