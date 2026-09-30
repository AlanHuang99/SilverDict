import importlib.util
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('resource_paths', Path(__file__).parents[1] / 'server/app/resource_paths.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

def test_extraction_cannot_escape(tmp_path):
    for name in ('../escape', '/etc/passwd', 'C:/Windows/test', '..\\escape'):
        with pytest.raises(ValueError): mod.resource_path(tmp_path, name)
    assert mod.resource_path(tmp_path, 'audio\\word.mp3') == tmp_path / 'audio/word.mp3'

def test_symlink_cannot_escape(tmp_path):
    (tmp_path/'link').symlink_to('/tmp', target_is_directory=True)
    with pytest.raises(ValueError): mod.resource_path(tmp_path, 'link/escape')

def test_extension_only_assets_preserve_reader_state_boundaries(tmp_path):
    assert mod.resource_path(tmp_path, 'images/.css') == tmp_path/'images/.css'
    assert mod.resource_path(tmp_path, 'images/.png') == tmp_path/'images/.png'
    for name in ('.resources-complete', 'mdx.pickle', '.cache/style.css', '../.css', '.pickle'):
        with pytest.raises(ValueError): mod.resource_path(tmp_path, name)

def test_copy_preserves_nested_fonts_and_original_css(tmp_path):
    src=tmp_path/'src'; dst=tmp_path/'dst'; (src/'fonts').mkdir(parents=True)
    (src/'fonts'/'test.woff2').write_bytes(b'font')
    (src/'book.css').write_text('body {color:red}')
    (src/'book.mdx').write_bytes(b'not an asset')
    mod.copy_assets(src,dst)
    assert (dst/'fonts'/'test.woff2').read_bytes()==b'font'
    (dst/'book.css').write_text('modified')
    assert (src/'book.css').read_text()=='body {color:red}'
    assert not (dst/'book.mdx').exists()

def test_copy_rejects_external_symlink(tmp_path):
    src=tmp_path/'src';src.mkdir();(src/'escape.css').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):mod.copy_assets(src,tmp_path/'dst')
