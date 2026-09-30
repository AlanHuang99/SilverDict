"""Bounded on-demand decoding of cached Ogg Speex pronunciation clips."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import wave

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_DURATION_SECONDS = 120
DECODE_TIMEOUT_SECONDS = 20
SAMPLE_RATE = 24000
_LOCK = threading.Lock()


def _source_file(source, root):
    source = Path(source).absolute()
    if source.suffix.lower() != '.spx' or not source.is_relative_to(root):
        raise ValueError('Audio source must be a cached .spx file')
    for part in (source, *source.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('Symbolic link audio paths are not allowed')
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError('Audio source must be a regular file inside cache root')
    return resolved


def _validate_wave(path):
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError('Decoded audio exceeds output size limit')
    try:
        with wave.open(str(path), 'rb') as audio:
            if audio.getnchannels() != 1 or audio.getframerate() != SAMPLE_RATE or audio.getsampwidth() != 2:
                raise ValueError('Unexpected decoded audio format')
            if not 0 < audio.getnframes() / SAMPLE_RATE <= MAX_DURATION_SECONDS:
                raise ValueError('Decoded audio exceeds duration limit or is empty')
            # Ensure a truncated WAV is never published even if its header is plausible.
            remaining = audio.getnframes()
            while remaining:
                block = audio.readframes(min(remaining, 32768))
                if not block or len(block) % 2:
                    raise ValueError('Truncated decoded audio')
                remaining -= len(block) // 2
    except (wave.Error, EOFError) as exc:
        raise ValueError('Invalid decoded WAV output') from exc


def _make_room(directory, size):
    files = sorted((p for p in directory.glob('*.wav') if p.is_file() and not p.is_symlink()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for path in files:
        if total + size <= MAX_CACHE_BYTES:
            break
        total -= path.stat().st_size
        path.unlink()
    if total + size > MAX_CACHE_BYTES:
        raise ValueError('Decoded audio exceeds cache budget')


def render_audio(source: Path, cache_root: Path) -> Path:
    """Return a mono PCM WAV, preserving the confined source and publishing atomically.

    Single application process is required by the library service. A bounded
    mutex serializes decoding; generated WAV cache eviction never touches sources.
    """
    root = Path(cache_root).resolve(strict=True)
    source = _source_file(source, root)
    with source.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_SOURCE_BYTES:
            raise ValueError('Audio source exceeds size limit or is empty')
        digest = hashlib.sha256(b'silverdict-pcm-mono-24000-v1\0')
        size = 0
        while block := stream.read(65536):
            size += len(block)
            if size > MAX_SOURCE_BYTES:
                raise ValueError('Audio source exceeds size limit')
            digest.update(block)
    directory = root / '.audio-wav'
    if directory.is_symlink():
        raise ValueError('Symbolic link audio cache is not allowed')
    directory.mkdir(exist_ok=True)
    output = directory / (digest.hexdigest() + '.wav')
    if not _LOCK.acquire(timeout=DECODE_TIMEOUT_SECONDS + 5):
        raise ValueError('Audio decoder is busy; retry shortly')
    temporary = None
    try:
        if output.is_symlink():
            raise ValueError('Symbolic link decoded audio is not allowed')
        if output.is_file():
            _validate_wave(output)
            os.utime(output, None)
            return output
        fd, temporary_name = tempfile.mkstemp(prefix='decode-', suffix='.part', dir=directory)
        os.close(fd)
        temporary = Path(temporary_name)
        # Force the Ogg demuxer: a disguised playlist must not read arbitrary files
        # or fetch remote URLs. Bound probe effort, threads, duration and file bytes.
        argv = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-max_alloc', str(64 * 1024 * 1024), '-threads', '1',
                '-probesize', '1048576', '-analyzeduration', '2000000',
                '-protocol_whitelist', 'file', '-f', 'ogg', '-i', str(source),
                '-map', '0:a:0', '-vn', '-sn', '-dn', '-threads', '1',
                '-t', str(MAX_DURATION_SECONDS + 1), '-ac', '1', '-ar', str(SAMPLE_RATE),
                '-c:a', 'pcm_s16le', '-fs', str(MAX_OUTPUT_BYTES), '-f', 'wav', str(temporary)]
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=DECODE_TIMEOUT_SECONDS, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ValueError('Audio decode failed or timed out') from exc
        if result.returncode:
            raise ValueError('Audio decode failed')
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Audio source changed during decoding; retry')
        _validate_wave(temporary)
        _make_room(directory, temporary.stat().st_size)
        temporary.replace(output)
        return output
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _LOCK.release()
