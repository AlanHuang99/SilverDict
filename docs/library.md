# Personal dictionary library

This fork combines SilverDict's native dictionary readers with a PyGlossary
conversion worker and a browser library. It supports searching selected groups,
importing files from a local collection, viewing background job progress, and
exporting HTML StarDict packages with resources and provenance.

## Local development

Python 3.12 or later is required. Install the pinned dependencies in
`requirements-library.lock` in a virtual environment. On Debian-based systems,
compiling python-lzo requires a C compiler, libc development headers and
liblzo2-dev. Run `python -m pytest tests -q` after installing pytest.

Set `DICTIONARY_DIR` to an existing collection, create `local-state` and
`local-library` writable by UID 1000, and run:

```sh
docker compose -f compose.example.yaml up --build -d
```

Browse `http://localhost:2628`. Local directories are ignored by Git. The example
binds only to loopback. For a hosted instance, set `SILVERDICT_PUBLIC_URL` to its
canonical HTTPS origin and terminate TLS at a trusted reverse proxy.

## State and source mounts

| Mount | Purpose |
| --- | --- |
| `/dictionaries`, read-only | Original dictionary collection |
| `/state` | Settings, SQLite search index and durable job history |
| `/library` | Extracted caches, staging, imported conversions and export ZIPs |

The process runs as UID/GID 1000. Native imports retain original MDX/MDD and
StarDict files. DSL inputs are copied before SilverDict's compression step.
StarDict resources are copied into a writable serving cache. Conversion never
replaces a native dictionary automatically. Dictionary content stays out of Git
and container images.

Use one application process; a file lock prevents multiple job coordinators.
The bundled Waitress entry point uses one process with request threads. Imports
are serialized. PyGlossary runs in a separate subprocess with a 4 GiB address-space
limit and a default four-hour timeout. The Compose example limits the entire
application to 6 GiB and two CPUs. Resource and archive limits are configurable
with `SILVERDICT_MAX_ARCHIVE_BYTES`, `SILVERDICT_CONVERSION_MEMORY_MB`, and
`SILVERDICT_CONVERSION_TIMEOUT`.

Jobs survive restarts: queued jobs resume; interrupted running jobs become failed
and explicitly retryable. Completed exports remain downloadable. Failed or
partial conversions are never published. Keep writable state and large data on
reliable storage, and back up settings/index/job state with writers stopped.

## Formats and fidelity

Native readers support MDX/MDD, StarDict and DSL. Additional explicitly mapped
input formats include TSV/TXT, CSV, XDXF and BGL, converted through PyGlossary.
ZIP import requires one dictionary with companion resources and rejects path
escapes, symbolic links, encrypted entries and oversized expansion. RAR, 7z and
ISO remain visible but require separate extraction. Collection discovery counts
files, not unique editions; it never deduplicates or deletes originals.

StarDict exports use HTML entries and 64-bit index offsets, with `.ifo`, `.idx`,
`.dict`, optional `.syn`, `res/` and `manifest.json`. The manifest records the
converter commit, options, output counts, warnings and source identity. Audio
references, aliases and nested font assets are handled explicitly. Legacy Speex
pronunciations are decoded on demand to a bounded WAV cache for browser playback;
the original `.spx` assets remain unchanged. A static
resource audit reports missing local references; JavaScript-generated requests
still need browser verification. Logged conversion errors fail the job. Chained
or unresolved MDX aliases currently fail with an explanation instead of silently
losing entries. A format conversion does not establish permission to distribute
the underlying content.

Search preserves healthy dictionary results when another source cannot read an entry,
and shows a warning naming the affected dictionary. Truncated MDX record data is
reported explicitly; conversion cannot reconstruct missing source bytes.

Dictionary articles run in opaque-origin sandboxed frames. Their scripts can
load local cache resources but cannot call management APIs or access the parent
library document. External resources are blocked; dictionaries dependent on
remote content may display incompletely. Legacy management routes are disabled
in library mode. This is a personal trusted-network deployment, not a public
multiuser account system.

## Images and updates

`.github/workflows/library.yml` runs tests and a container build for pull requests
and the development branch. Tags beginning `library-v` publish to
`ghcr.io/alanhuang99/silverdict` using the workflow's `GITHUB_TOKEN`. Actions and
the base image are pinned by commit/digest; Python dependencies and converter
source are pinned. Upstream's DockerHub workflow is restricted to its upstream
repository.

Deploy a reviewed version by digest in a separate host Compose repository. Pull
that image, back up state while stopped, recreate only this application and test
lookup, media, exports and HTTPS. Preserve the prior image digest and state backup
for rollback. Publishing an image never automatically updates a host deployment.

The container health endpoint is `/api/library/health`. The UI's Collection tab
shows source status; Activity shows per-job failures and conversion warnings.
Full-text indexing, arbitrary archive extraction and user accounts are outside
this first release.
