# Implementation ledger: personal dictionary library

Specification and repository separation approved September 30, 2026. The user
authorized implementation, publishing and deployment. Application source lives
in its own fork; the host deployment repository contains Compose and operations.

- Backend complete: durable serialized imports, groups, isolated PyGlossary
  conversion, HTML StarDict exports, atomic settings and resource limits.
- UI complete: search across groups, collection selection, activity/retry/downloads,
  opaque sandboxed article frames and local resources. Imported dictionaries can
  be added to another group. External article scripts load before inline initializers.
- Packaging complete: pinned dependencies/base image/actions, nonroot Docker image,
  PR tests/builds and GHCR release publishing. GitHub-built versioned images
  have been pulled and deployed successfully.
- Independent backend/UI/final reviews completed. Confirmed findings were fixed
  and scoped re-reviews closed them. All 75 Python tests pass, including bounded
  Speex decoding, corrupt-reader isolation, retry recovery, resource confinement
  simultaneous index readers/writers, confined local webfonts and CSS comment
  handling. JavaScript syntax checks pass.
- Real acceptance: native photo and bilingual dictionaries render. A real export
  contains 3,633 readable entries and 429 aliases. Static audit identifies a
  missing original stylesheet. Browser rejects article access to the parent DOM
  and management API. Large Longman MDX/MDD imports successfully.
- Host HTTPS onboarding passed with certificate verification enabled, alongside
  browser lookup, illustration, pronunciation playback and mobile layout checks.
  The user selected a curated SSD collection as the sole source. Its imports and
  source-specific recovery are tracked in the private deployment runbook.
- Pronunciation controls use compact keyboard-accessible speaker buttons with
  loading, pause and retry states. Optional shared local webfonts preserve
  dictionary-provided fonts and avoid installing server fonts as a substitute for
  browser delivery.
- Lookup failures return JSON and preserve healthy dictionary results. Legacy
  bundles with Finder metadata and extension-only asset names import safely.
  Full block audits distinguish source corruption from application errors.

Private source inventories, screenshots, dictionary content and host job reports
remain outside this public repository.

September 30 reading update: added index-only search, entry rendering on expansion,
frame teardown on collapse, cached reopening and obsolete-request cancellation.
Reading Settings persists per-group selection/order with atomic replacement;
new imports remain enabled and stale settings cannot drop newly imported members.
All 86 Python tests pass. Browser prototype checks pass for keyboard toggling,
deferred frame/request counts, cached reopen, reordered/disabled results after
reload and phone-width layout. GitHub run 36764730239 published v0.1.6; the exact digest was pulled and deployed.
Live HTTPS checks passed persistence across reload, deferred requests, cached
reopening, all-entry expand/collapse, pronunciation and mobile layout. The local
browser first-entry measurement for a 22-match query improved from 6.081 seconds
to 0.313 seconds with the default reading order.
