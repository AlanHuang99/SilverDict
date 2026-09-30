"""Confined, copy-on-read resources shared by dictionary readers."""
from pathlib import Path, PurePosixPath
import shutil

ASSET_SUFFIXES = {'.css', '.js', '.json', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico', '.mp3', '.wav', '.ogg', '.spx', '.mp4', '.woff', '.woff2', '.ttf', '.otf'}

def resource_path(root, name):
    root = Path(root).resolve()
    name = str(name).replace('\\', '/')
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or ':' in name or not name or '\x00' in name or parts.suffix == '.pickle' or any(p.startswith('.') for p in parts.parts):
        raise ValueError('Invalid dictionary resource path')
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError('Resource escapes its dictionary directory')
    return candidate

def copy_assets(source, destination):
    source = Path(source).resolve()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob('*'):
        if path.is_symlink():
            if not path.resolve().is_relative_to(source):
                raise ValueError('Resource symlink escapes source directory')
            continue
        if not path.is_file() or path.suffix.lower() not in ASSET_SUFFIXES:
            continue
        target = resource_path(destination, path.relative_to(source).as_posix())
        if not target.exists() or target.stat().st_mtime_ns < path.stat().st_mtime_ns:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
