"""Isolated conversion, ZIP validation and atomic export support.

The worker is executed as a file, avoiding application initialization in its process.
"""
import json
import codecs
from contextlib import contextmanager
from html.parser import HTMLParser
import re
from urllib.parse import unquote, urlsplit
import filecmp
import sqlite3
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys
import zipfile

MAX_ARCHIVE_BYTES = int(os.environ.get('SILVERDICT_MAX_ARCHIVE_BYTES', 20 * 1024**3))
MAX_ARCHIVE_FILES = 100000
ASSET_SUFFIXES = {'.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.mp3', '.wav', '.ogg', '.mp4', '.woff', '.woff2', '.ttf', '.otf', '.eot'}


def safe_member(name):
    path = PurePosixPath(name.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or not path.parts or ':' in path.parts[0]:
        raise ValueError('Unsafe resource or archive path')
    return Path(*path.parts)


def extract_zip(source, target):
    target = Path(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_FILES or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError('Archive exceeds extraction limits')
        seen = set()
        for entry in entries:
            relative = safe_member(entry.filename)
            if relative in seen or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                raise ValueError('Duplicate, symbolic link or encrypted archive member')
            seen.add(relative)
            if entry.file_size > max(entry.compress_size, 1) * 1000:
                raise ValueError('Archive compression ratio exceeds limit')
        total = 0
        for entry in entries:
            output = target / safe_member(entry.filename)
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as inp, output.open('xb') as out:
                while block := inp.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError('Archive exceeds extraction limit')
                    out.write(block)
    return target


class ResourceBudget:
    def __init__(self, max_bytes=None, max_files=None):
        self.max_bytes = MAX_ARCHIVE_BYTES if max_bytes is None else max_bytes
        self.max_files = MAX_ARCHIVE_FILES if max_files is None else max_files
        self.bytes = self.files = 0

    def reserve(self, size):
        if size < 0 or self.bytes + size > self.max_bytes or self.files + 1 > self.max_files:
            raise ValueError('Decoded resource byte/file limit exceeded')
        self.bytes += size
        self.files += 1


@contextmanager
def resource_guards(entry_type, max_bytes=None, max_files=None, output_directory=None):
    """Bound resource decoding and saving, including converter temporary files."""
    decoded = ResourceBudget(max_bytes, max_files)
    saved = ResourceBudget(max_bytes, max_files)
    temporary = ResourceBudget(max_bytes, max_files)
    final_root = Path(output_directory).resolve() if output_directory is not None else None
    original_init, original_save = entry_type.__init__, entry_type.save
    def guarded_init(self, fname, data=None, tmpPath=None, byteProgress=None):
        safe_member(fname)
        if data is not None:
            decoded.reserve(len(data))
        original_init(self, fname, data=data, tmpPath=tmpPath, byteProgress=byteProgress)
    def guarded_save(self, directory):
        safe_member(self.getFileName())
        # PyGlossary saves each resource to its SQLite cache before saving it
        # again to StarDict res/. Bound these phases independently.
        destination = Path(directory).resolve()
        budget = saved if final_root is None or destination.is_relative_to(final_root) else temporary
        budget.reserve(self.size())
        return original_save(self, directory)
    entry_type.__init__, entry_type.save = guarded_init, guarded_save
    try:
        yield saved
    finally:
        entry_type.__init__, entry_type.save = original_init, original_save



def copy_assets(source, destination, budget=None):
    """Copy nested sidecar assets without traversing links or other dictionaries."""
    source, destination = Path(source), Path(destination)
    budget = budget or ResourceBudget()
    for directory, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink() and not d.startswith('.')]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() not in ASSET_SUFFIXES:
                continue
            if path.is_symlink():
                raise ValueError('Symbolic link resource is not permitted')
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not filecmp.cmp(target, path, shallow=False):
                    raise ValueError('Conflicting resource files')
                continue
            budget.reserve(path.stat().st_size)
            shutil.copyfile(path, target)


def validate_stardict(ifo):
    """Stream index and synonym validation, including aliases pointing past EOF."""
    ifo = Path(ifo)
    metadata = dict(line.split('=', 1) for line in ifo.read_text('utf-8').splitlines() if '=' in line)
    count = int(metadata.get('wordcount', '0'))
    if count < 1 or metadata.get('sametypesequence') != 'h':
        raise ValueError('Output is not a nonempty HTML StarDict')
    base = ifo.with_suffix('')
    data_size = base.with_suffix('.dict').stat().st_size
    bits = int(metadata.get('idxoffsetbits', '32'))
    if bits not in (32, 64):
        raise ValueError('Unsupported StarDict index offset size')
    record_size = 12 if bits == 64 else 8
    actual = 0
    with base.with_suffix('.idx').open('rb') as stream:
        while True:
            first = stream.read(1)
            if not first:
                break
            word = bytearray(first)
            while word[-1] != 0:
                char = stream.read(1)
                if not char or len(word) > 1024 * 1024:
                    raise ValueError('Invalid StarDict index')
                word.extend(char)
            offset_size = stream.read(record_size)
            if len(offset_size) != record_size:
                raise ValueError('Truncated StarDict index')
            offset, size = struct.unpack('>QI' if bits == 64 else '>II', offset_size)
            if offset + size > data_size:
                raise ValueError('StarDict entry exceeds data file')
            actual += 1
    if actual != count:
        raise ValueError('StarDict word count mismatch')
    aliases = 0
    syn = base.with_suffix('.syn')
    if syn.exists():
        with syn.open('rb') as stream:
            while first := stream.read(1):
                length = 1
                while first != b'\0':
                    first = stream.read(1)
                    length += 1
                    if not first or length > 1024 * 1024:
                        raise ValueError('Invalid alias')
                index = stream.read(4)
                if len(index) != 4 or struct.unpack('>I', index)[0] >= count:
                    raise ValueError('Alias points outside dictionary')
                aliases += 1
    if aliases != int(metadata.get('synwordcount', '0')):
        raise ValueError('Alias count mismatch')
    return {'entries': count, 'aliases': aliases}


CSS_REFERENCE = re.compile(r"url\(\s*['\"]?([^'\"()]+?)['\"]?\s*\)|@import\s+['\"]([^'\"]+)['\"]", re.I)


class ResourceAudit:
    """Bounded static reference audit; does not execute scripts or fetch URLs."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.checked = self.missing = self.external = 0
        self.samples = []
        self.complete = True

    def reference(self, value, base=None):
        value = value.strip()
        if not value or value.startswith('#'):
            return
        url = urlsplit(value.replace('\\', '/'))
        if url.scheme.lower() in ('entry', 'bword', 'data', 'javascript', 'mailto'):
            return
        if url.scheme.lower() in ('http', 'https') or url.netloc:
            self.external += 1
            return
        self.checked += 1
        relative = unquote(url.path).lstrip('/')
        candidates = [Path(base) / relative] if base else [self.root / 'res' / relative, self.root / relative]
        valid = False
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_relative_to(self.root) and resolved.is_file() and not candidate.is_symlink():
                valid = True
                break
        if not valid:
            self.missing += 1
            if len(self.samples) < 100 and value[:1000] not in self.samples:
                self.samples.append(value[:1000])

    def css(self, text, base=None):
        for match in CSS_REFERENCE.finditer(text):
            self.reference(match.group(1) or match.group(2), base)

    def result(self):
        return {'checked_references': self.checked, 'missing_references': self.missing,
                'missing_samples': self.samples, 'external_references': self.external,
                'audit_complete': self.complete, 'scope': 'Static HTML and CSS; scripts are not executed'}


class ArticleReferences(HTMLParser):
    def __init__(self, audit):
        super().__init__(convert_charrefs=True)
        self.audit = audit
        self.in_style = False
        self.style_buffer = ''

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'style':
            self.in_style = True
        for key in ('src', 'poster', 'data'):
            if attrs.get(key):
                self.audit.reference(attrs[key])
        if attrs.get('href') and tag == 'link':
            self.audit.reference(attrs['href'])
        if attrs.get('style'):
            self.audit.css(attrs['style'])
        if attrs.get('srcset') and not attrs['srcset'].startswith('data:'):
            for value in attrs['srcset'].split(','):
                if value.strip():
                    self.audit.reference(value.strip().split()[0])

    def handle_endtag(self, tag):
        if tag == 'style':
            self.audit.css(self.style_buffer)
            self.style_buffer = ''
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.style_buffer += data
            if len(self.style_buffer) > 1024 * 1024:
                self.audit.css(self.style_buffer)
                self.style_buffer = ''
                self.audit.complete = False


def audit_resources(ifo):
    ifo = Path(ifo)
    audit = ResourceAudit(ifo.parent)
    metadata = dict(line.split('=', 1) for line in ifo.read_text('utf-8').splitlines() if '=' in line)
    width = 12 if metadata.get('idxoffsetbits') == '64' else 8
    with ifo.with_suffix('.idx').open('rb') as index, ifo.with_suffix('.dict').open('rb') as articles:
        while first := index.read(1):
            while first != b'\0':
                first = index.read(1)
                if not first:
                    raise ValueError('Truncated index during resource audit')
            offset, size = struct.unpack('>QI' if width == 12 else '>II', index.read(width))
            articles.seek(offset)
            parser = ArticleReferences(audit)
            decoder = codecs.getincrementaldecoder('utf-8')('strict')
            remaining = size
            while remaining:
                block = articles.read(min(65536, remaining))
                if not block:
                    raise ValueError('Truncated article during resource audit')
                remaining -= len(block)
                parser.feed(decoder.decode(block, final=not remaining))
                if len(parser.rawdata) > 1024 * 1024:
                    audit.complete = False
                    parser.reset()
            parser.close()
            if parser.style_buffer:
                audit.css(parser.style_buffer)
    for css in ifo.parent.rglob('*.css'):
        if css.is_symlink():
            raise ValueError('Symbolic link CSS is not allowed')
        with css.open('r', encoding='utf-8', errors='strict') as stream:
            while text := stream.read(1024 * 1024):
                # Retain a complete line where practical; unusually large tokens are flagged.
                tail = stream.readline(65536)
                audit.css(text + tail, css.parent)
                if len(tail) == 65536 and not tail.endswith('\n'):
                    audit.complete = False
    return audit.result()


class StrictLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.errors = 0
        self.warnings = []

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.errors += 1
        if len(self.warnings) < 200:
            self.warnings.append(record.getMessage()[:2000])


def validate_source_bundle(source):
    # Reject linked companions before handing a path to any third-party reader.
    for directory, dirs, files in os.walk(Path(source).parent, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError('Symbolic link in source bundle is not permitted')


def validate_mdx_aliases(source, database):
    from pyglossary.plugin_lib.readmdict import MDX
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE words(word TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE aliases(word TEXT, target TEXT)')
        for term, definition in MDX(str(source)).items():
            word = term.decode('utf-8')
            definition = definition.decode('utf-8').strip()
            if definition.startswith('@@@LINK='):
                db.execute('INSERT INTO aliases VALUES(?,?)', (word, definition[8:]))
            else:
                db.execute('INSERT OR IGNORE INTO words VALUES(?)', (word,))
        broken = db.execute('SELECT aliases.word FROM aliases LEFT JOIN words ON aliases.target=words.word WHERE words.word IS NULL LIMIT 1').fetchone()
        if broken:
            raise ValueError('MDX has unresolved or chained alias: ' + broken[0])


def worker(source, fmt, output):
    import resource
    memory = int(os.environ.get('SILVERDICT_CONVERSION_MEMORY_MB', '4096')) * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (14400, 14400))
    # Configure converter paths before importing modules which capture these globals.
    # PyGlossary currently ignores XDG_CACHE_HOME on Linux.
    import pyglossary.core as core
    core.cacheDir = str(Path(output).parent / 'converter-cache')
    core.confDir = str(Path(output).parent / 'converter-config')
    core.userPluginsDir = str(Path(core.confDir) / 'plugins')
    core.confJsonFile = str(Path(core.confDir) / 'config.json')
    from pyglossary.glossary_v2 import Glossary, ConvertArgs
    from pyglossary.entry import DataEntry
    log = StrictLog()
    logging.getLogger('pyglossary').addHandler(log)
    validate_source_bundle(source)
    read_options = {}
    if fmt == 'MDict':
        read_options = {'audio': True}
        validate_mdx_aliases(source, Path(output).parent / 'aliases.sqlite3')
        fmt = 'OctopusMdict'
    Glossary.init()
    glossary = Glossary()
    options = {'sametypesequence': 'h', 'dictzip': False, 'large_file': True}
    with resource_guards(DataEntry, output_directory=Path(output) / 'res') as resource_budget:
        result = glossary.convert(ConvertArgs(inputFilename=source, inputFormat=fmt, outputFilename=str(Path(output) / 'dictionary.ifo'), outputFormat='Stardict', sqlite=True, readOptions=read_options, writeOptions=options))
        if not result or log.errors:
            raise ValueError(f'Conversion failed with {log.errors} logged errors: ' + '; '.join(log.warnings[-5:]))
        copy_assets(Path(source).parent, Path(output) / 'res', budget=resource_budget)
        counts = validate_stardict(Path(output) / 'dictionary.ifo')
        audit = audit_resources(Path(output) / 'dictionary.ifo')
        if audit['missing_references']:
            log.warnings.append(f"{audit['missing_references']} unresolved local resource references; inspect resource_audit")
        if audit['external_references']:
            log.warnings.append(f"{audit['external_references']} external resource references require network access and are not embedded")
        if not audit['audit_complete']:
            log.warnings.append('Static resource audit incomplete for oversized HTML/CSS tokens')
        manifest = {'readOptions': read_options, 'resource_audit': audit, 'converter': 'PyGlossary', 'commit': 'd6e679d73a5bbf7dfc0214595becd5c7c875b3c3', 'format': fmt, 'options': options, 'warnings': log.warnings, **counts}
        (Path(output) / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def convert(source, fmt, output):
    output = Path(output)
    output.mkdir(parents=True)
    logpath = output.parent / 'conversion.log'
    env = os.environ.copy()
    env['TMPDIR'] = str(output.parent)
    with logpath.open('wb') as log:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(source), fmt, str(output)], cwd=output.parent, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=int(os.environ.get('SILVERDICT_CONVERSION_TIMEOUT', '14400')))
    if result.returncode:
        with logpath.open('rb') as log:
            log.seek(max(0, logpath.stat().st_size - 4000))
            detail = log.read().decode('utf-8', 'replace')
        raise ValueError('Conversion subprocess failed: ' + detail)
    validate_stardict(output / 'dictionary.ifo')
    return output / 'dictionary.ifo'


if __name__ == '__main__':
    worker(*sys.argv[1:])
