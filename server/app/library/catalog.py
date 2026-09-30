"""Read-only discovery and confined source paths."""
import hashlib
import os
from pathlib import Path

NATIVE = {'MDict': 'MDict (.mdx)', 'Stardict': 'StarDict (.ifo)', 'ABBYYLingvoDSL': 'DSL (.dsl/.dsl.dz)'}
EXTENSIONS = {'.mdx': 'MDict', '.ifo': 'Stardict', '.dsl': 'ABBYYLingvoDSL', '.dsl.dz': 'ABBYYLingvoDSL', '.tsv': 'Tabfile', '.txt': 'Tabfile', '.csv': 'Csv', '.xdxf': 'Xdxf', '.xdxf.gz': 'Xdxf', '.bgl': 'BabylonBgl', '.zip': 'ZIP', '.rar': 'unsupported archive', '.7z': 'unsupported archive', '.iso': 'unsupported archive'}


def source_id(relative):
    return hashlib.sha256(str(relative).encode('utf-8')).hexdigest()[:24]


def format_of(path):
    return next((value for suffix, value in sorted(EXTENSIONS.items(), key=lambda x: -len(x[0])) if str(path).lower().endswith(suffix)), None)


def confined(root, relative):
    root = Path(root).resolve(strict=True)
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('Source path escapes allowed root')
    path = root / rel
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('Symbolic links are not allowed in source paths')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError('Invalid source file')
    return resolved


class Catalog:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)

    def scan(self):
        items = []
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink() and not d.startswith('.') and d != 'res' and not d.endswith('.files'))
            for name in sorted(files):
                path = Path(directory) / name
                fmt = format_of(name)
                if not fmt or path.is_symlink() or '_abrv.dsl' in name:
                    continue
                relative = path.relative_to(self.root).as_posix()
                items.append({'id': source_id(relative), 'title': path.stem, 'path': relative, 'format': fmt, 'bytes': path.stat().st_size, 'status': 'unsupported' if fmt == 'unsupported archive' else 'available', 'dictionary_id': None, 'error': None})
        return items

    def get(self, identity):
        item = next((item for item in self.scan() if item['id'] == identity), None)
        if item is None:
            raise ValueError('Unknown source ID')
        return item, confined(self.root, item['path'])
