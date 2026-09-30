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
