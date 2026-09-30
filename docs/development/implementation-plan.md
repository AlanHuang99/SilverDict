# Personal library implementation plan

> For agentic workers: use superpowers:subagent-driven-development for the backend task and independent reviews, with the coordinating agent implementing integration, packaging and deployment.

Goal: serve a personal dictionary collection with optional portable StarDict conversion, built on GitHub and deployed by image digest.
Architecture: SilverDict remains the reader engine. A Flask library extension owns a catalog and persistent jobs; one background coordinator serializes imports and launches isolated PyGlossary conversion subprocesses. A responsive library UI renders articles in sandboxed frames. Application code and image builds remain separate from host Compose configuration.
Spec: this document consolidates the approved architecture and public requirements; private collection research is retained outside the Git repository.

## Global constraints

- Python >=3.12. Pin PyGlossary commit d6e679d73a5bbf7dfc0214595becd5c7c875b3c3.
- Originals are read-only. Never delete source dictionaries. Durable settings/jobs under SILVERDICT_STATE; writable staging/exports under SILVERDICT_LIBRARY_DATA; input root SILVERDICT_SOURCE.
- Synthetic fixtures only in Git and images. No credentials or personal file inventories in Git.
- One conversion at a time. No shell interpolation of dictionary paths. No partial output publication.
- Dictionary scripts cannot access application management. Mutations require same-origin requests with X-SilverDict-Library: 1; no permissive CORS.
- Native imports: MDX, StarDict, DSL. Other supported PyGlossary inputs convert to HTML StarDict. Exports always preserve originals and carry manifest/options/warnings.
- Archive import is explicit ZIP only initially, with traversal/symlink/size controls. RAR/7z/ISO stay visible as unsupported archives.
- App source in this separate repository; deployment config only in the homelab repository. Deploy via GHCR digest; verify canonical HTTPS in real browser.

## Review focus

Traversal and symlink escape; restart recovery after interrupted jobs; missing nested resources and aliases; same-name dictionary IDs; untrusted HTML/scripts reaching management.

## Task 1: Catalog, jobs and conversion backend

Files: create server/app/library/{__init__,catalog,jobs,convert}.py, tests/test_library*.py. Avoid editing existing files: root agent wires create_app and patches readers.
Interfaces: init_library(app) registers blueprint and coordinator using app.extensions['dictionaries']; returns library service in app.extensions['library'].
API (all prefixed /api/library):
GET /catalog -> {items:[{id,title,path,format,bytes,status,dictionary_id,error}],groups:[string]}; paths relative to source, IDs deterministic SHA256 path prefixes. Include registered/exported entries where needed.
POST /jobs JSON {source_id,action:'import'|'export',group:'Default Group'} -> {id,status}; status 202. Import native directly, convert other types first; export to StarDict without automatic native replacement.
GET /jobs -> {jobs:[{id,source_id,action,status,progress,message,download_url,created_at}]}; GET /jobs/<id>/download serves completed ZIP export.
GET /search?q=...&group=... -> {articles:[{id,title,html}],suggestions:[string]}; empty groups safe. Existing dicts.query returns (name,title,html). Root frontend handles sandbox rendering.
GET /health -> {status:'ok'}.
Errors use {error:string}, appropriate 4xx.
Tests: root confinement including symlinks, identity collisions, queue dedup, interrupted job recovery, failed conversion not published, resource/log errors fail export, safe archives, aliases, nested assets. Use unittest/pytest and temporary directories; stub reader registry for API unit tests and real tiny TSV conversion smoke where available.
Steps: [ ] failing boundary tests; [ ] implement catalog/job store and serialization; [ ] isolated conversion and validation; [ ] API tests pass; [ ] report changed files/tests/limitations. Do not commit other agents' files.

## Task 2: Library UI and safe readers

Files: server/app/library static HTML/CSS/JS via root-owned server/library_ui directory; wire server/app/__init__.py; patch resource readers and settings env paths; tests/test_reader_safety.py.
Deliverable: root library UI search/groups, catalog selection, import/export, progress and downloads; mobile layout; sandboxed frame allow-scripts without allow-same-origin, strict CSP limiting resources to cache, intercepted entry links. Legacy management disabled in library mode. Read-only source assets copied to writable caches, including nested fonts. Reject extracted paths escaping cache. Correct audio/src rewrites and do not mutate StarDict originals.
Steps: [ ] boundary tests; [ ] implement; [ ] Python tests and JS syntax check; [ ] browser search/import/export acceptance.

## Task 3: Reproducible image and publishing

Files: Dockerfile.library, requirements-library.lock, .dockerignore, .github/workflows/library.yml, compose.example.yaml, docs/library.md.
Deliverable: nonroot Python 3.12 image, pinned converter source and Python dependencies, healthcheck; PR test builds without push and version-tag publishing to ghcr.io/alanhuang99/silverdict. Preserve upstream workflow separately. Test local image, create feature PR, publish explicit version tag from tested commit. User explicitly authorized all implementation, forks, image publishing and deployment on September 30.

## Task 4: Homelab deployment and collection acceptance

Files in homelab only: stacks/documents-silverdict/compose.yaml and .env.example; inventory/services.yaml; Caddyfile; dashboard links and runbook. Read project memory/storage/new-service rules before deployment. Pin GHCR digest; source read-only, state SSD, large caches/generated exports HDD with UUID gate. Use maintenance lock. Verify small real native dictionaries and conversion assets before importing 95 unpacked MDX files. Preserve per-dictionary failures visibly. No bulk archive extraction. Verify TLS, mobile/desktop browser, monitoring, restart and second reconcile.

## Execution rulings

User's 'go for all, full speed' authorizes continuous implementation without another design/plan checkpoint. Work in this dedicated clean checkout on codex/personal-dictionary-library, isolated from homelab configuration. External creation target is authenticated personal account AlanHuang99; public upstream fork contains code only. Deployments are explicitly authorized in this turn.
