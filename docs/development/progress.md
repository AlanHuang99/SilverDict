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
  PR tests/builds and GHCR release publishing. Initial GitHub checks passed.
- Independent backend/UI/final reviews completed. Confirmed findings were fixed
  and scoped re-reviews closed them. Speex browser compatibility added during
  real collection acceptance; its helper is undergoing focused verification.
- Real acceptance: native photo and bilingual dictionaries render. A real export
  contains 3,633 readable entries and 429 aliases. Static audit identifies a
  missing original stylesheet. Browser rejects article access to the parent DOM
  and management API. Large Longman MDX/MDD imports successfully.
- Host onboarding and full collection import remain in progress. Do not interpret
  container or API health alone as final HTTPS/browser/media acceptance.

Private source inventories, screenshots, dictionary content and host job reports
remain outside this public repository.
