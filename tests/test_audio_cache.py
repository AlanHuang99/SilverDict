import importlib.util
from pathlib import Path
import shutil
import subprocess
import wave
import pytest

SPEC = importlib.util.spec_from_file_location('audio_cache_under_test', Path(__file__).parents[1] / 'server/app/audio_cache.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_audio_source_confinement_and_limits(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    cache.mkdir()
    outside = tmp_path / 'outside.spx'
    outside.write_bytes(b'fake')
    linked = cache / 'linked.spx'
    linked.symlink_to(outside)
    for source in (outside, linked):
        with pytest.raises(ValueError):
            mod.render_audio(source, cache)
    wrong = cache / 'wrong.mp3'
    wrong.write_bytes(b'fake')
    with pytest.raises(ValueError):
        mod.render_audio(wrong, cache)
    large = cache / 'large.spx'
    large.write_bytes(b'123')
    monkeypatch.setattr(mod, 'MAX_SOURCE_BYTES', 2)
    with pytest.raises(ValueError, match='size'):
        mod.render_audio(large, cache)


def test_decoder_failure_not_published(tmp_path, monkeypatch):
    source = tmp_path / 'broken.spx'
    source.write_bytes(b'invalid')
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1)
    monkeypatch.setattr(mod.subprocess, 'run', failed)
    with pytest.raises(ValueError, match='decode'):
        mod.render_audio(source, tmp_path)
    assert not list(tmp_path.rglob('*.wav'))
    assert source.read_bytes() == b'invalid'


def test_real_speex_to_wave_and_cached_reuse(tmp_path, monkeypatch):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg is unavailable')
    source = tmp_path / 'clip with spaces.spx'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', '-ar', '16000', '-c:a', 'libspeex', '-f', 'ogg', str(source)], check=True, timeout=15)
    original = source.read_bytes()
    output = mod.render_audio(source, tmp_path)
    with wave.open(str(output), 'rb') as audio:
        assert audio.getnchannels() == 1
        assert audio.getframerate() == 24000
        assert 0 < audio.getnframes() <= 24000
    assert source.read_bytes() == original
    def forbidden(*args, **kwargs):
        raise AssertionError('Cached response must not decode again')
    monkeypatch.setattr(mod.subprocess, 'run', forbidden)
    assert mod.render_audio(source, tmp_path) == output


def test_duration_limit_blocks_publication(tmp_path, monkeypatch):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg is unavailable')
    source = tmp_path / 'long.spx'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.3', '-ar', '16000', '-c:a', 'libspeex', '-f', 'ogg', str(source)], check=True, timeout=15)
    monkeypatch.setattr(mod, 'MAX_DURATION_SECONDS', 0.1)
    with pytest.raises(ValueError, match='duration'):
        mod.render_audio(source, tmp_path)
    assert not list(tmp_path.rglob('*.wav'))


def test_timeout_cleans_partial_audio(tmp_path, monkeypatch):
    source = tmp_path / 'clip.spx'
    source.write_bytes(b'fake')
    def timeout(argv, **kwargs):
        assert isinstance(argv, list)
        assert kwargs.get('shell', False) is False
        Path(argv[-1]).write_bytes(b'partial')
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    monkeypatch.setattr(mod.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='timed out'):
        mod.render_audio(source, tmp_path)
    assert not list((tmp_path / '.audio-wav').iterdir())


def test_output_cache_budget_evicts_generated_wavs_only(tmp_path, monkeypatch):
    source = tmp_path / 'clip.spx'
    source.write_bytes(b'fake')
    output_dir = tmp_path / '.audio-wav'
    output_dir.mkdir()
    old = output_dir / 'old.wav'
    old.write_bytes(b'0' * 600)
    monkeypatch.setattr(mod, 'MAX_CACHE_BYTES', 1000)
    def encode(argv, **kwargs):
        with wave.open(argv[-1], 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 240)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(mod.subprocess, 'run', encode)
    output = mod.render_audio(source, tmp_path)
    assert output.is_file() and not old.exists()
    assert source.read_bytes() == b'fake'
    assert sum(p.stat().st_size for p in output_dir.glob('*.wav')) <= 1000


def test_symlink_output_directory_rejected(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    source = cache / 'clip.spx'
    source.write_bytes(b'fake')
    (cache / '.audio-wav').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='Symbolic link'):
        mod.render_audio(source, cache)
