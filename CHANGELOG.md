# Changelog

## Unreleased

### Added

- The engine has the skeleton of its HTTP API (`engine/api`). Every request needs the per-session token
  and a local Host header, and no CORS header is ever sent.
- The engine API gained read routes under `/api`: the photos of a library, images served by photo ID
  only (a thumbnail, or the original on request), the provider status, the review session (read and
  save) and clearing a library's generated data. No route accepts a path. The routes are tested with
  synthetic images, and the engine test suite passes in a container (182 tests).
- The engine API can now start an analysis run as a child process (one at a time), pause, resume and
  cancel it, stream its progress events over server-sent events, and copy chosen photos into a library's
  output folder. A run request carries options only, never a path or a command, and exports only copy.
  The tests use a stub engine and synthetic images and pass in the container run (182 engine tests in
  total); the routes have not been run against the real engine.
- The services now run in Docker Compose: `gateway`, `api` and `vision`, with images pinned by digest,
  non-root users, all capabilities dropped and read-only root filesystems. Only `gateway` publishes a
  port, on `127.0.0.1`, and `api` and `vision` have no route to the internet. Photos mount read-only at
  `/photos`, with data and models in named volumes. `bash scripts/check.sh --integration` proves the
  isolation on a synthetic folder, using the image without PyTorch, no GPU and no model. The PyTorch image,
  GPU access and a served model have not been run, and the desktop app does not use the stack yet (P02.4).

### Changed

- Electron moves from 33.4.11, which was out of support, to 45.2.1, and `electron-builder` from 25.1.8 to
  26.18.0. Working on the app now needs Node.js 22.12 or newer, and the first `electron` run downloads the
  binary. The window hardening, navigation lock, CSP and `afmedia` scheme are unchanged. The upgraded app
  passes build, lint and tests but has not yet been started.

- Python now runs only in containers. The `engine:*` npm scripts that called Python on the host are
  removed, and the Electron main process no longer looks for a host interpreter or a `.venv`. Until
  P02.4 connects the containerised engine, starting an analysis from the app fails with the message
  "The analysis engine is not available yet", and engine-backed read-only checks report no result.
  Run the engine tests with the container command in `docs/testing.md`.

- The documentation now describes AuraFrame AI as a general tool for finding beautiful photos of any
  subject. Application behavior has not changed yet.
- The documentation now defines the target: an Electron desktop application whose services run in Docker,
  started with `start.sh`, a comparative curation pipeline, a recommended local model and an optional Codex
  CLI provider. All of this is planned.
- Project instructions and specialist agent definitions now follow the new direction.
- The engine no longer carries the retired single-purpose vocabulary. The model score field is now
  `overall_score` (`overallScore` in the data sent to the desktop app) and the ranking weight is
  `gemma_overall_weight`. The clothing score is now the neutral `style_score` (`styleScore` in the data
  sent to the desktop app, `style` column in the database, `style_context_weight` in the ranking
  configuration); the ranking arithmetic is unchanged. The local and cloud prompts now judge a photograph in general. The cloud flow
  keeps its behavior; only wording, the review field names (`overall_value`, `selection_value`, `appeal`)
  and the prompt versions changed, so cached cloud results are not reused.
- Export folders and report sections use neutral names (`top_06_final_set`, `top_20_best`,
  `top_50_review`). Analysis data from earlier runs must be recreated by a new run; there is no migration
  ([ADR 0001](docs/decisions/0001-general-purpose-photo-curation.md)).
- The desktop app (Electron contracts, Angular models, mock API, dashboard) now uses `overallScore` and
  `styleScore`, matching the engine, and the package description calls the product a local AI photo
  curation tool.
- The per-person collections are now named libraries across Electron, the preload bridge, Angular and
  the engine comments. Bridge methods are `getLibraryState`, `createLibrary`, `renameLibrary`,
  `activateLibrary` and `deleteLibrary`; IPC channels are `libraries:*`; the registry is `libraries.json`
  with data under `libraries/<id>/`. The slot type is now `PhotoSlot`, and the review screen route and
  screen are `library-builder`. The store test command is now `npm run test:libraries`.
- The library store no longer migrates data from the earlier flat layout and no longer creates a
  hard-coded personal name: a new installation starts with one library named "My library". Registries
  and data written by earlier versions are not read; analysis must be run again, as with the score rename
  above. The browser preview uses neutral sample library names.

### Added

- `tests/personal-data.test.cjs`, a guard test that fails, naming file and line, on a user-home path
  or on any word of at least four letters of the owner name (read from `git config user.name` at test
  time, never stored) in a tracked or unignored file. The package.json author field was removed.
- `tests/retired-terms.test.cjs`, a guard test that fails, naming file and line, when the retired
  product vocabulary reappears in any tracked or unignored file.
- `scripts/check.sh`, one entry point for lint, builds, Node tests and the containerized engine tests,
  with `--node`, `--engine` and `--integration` subsets and visible SKIP lines.
- Documentation index, requirements, roadmap with exit checks, testing guide with dated results, risks,
  repository assessment with verified defects, Claude Code guide with the approved skills, and nine
  decision records.

### Removed

- All previous documents: the old product specification, architecture, pipeline, design system, cloud
  mode guide, agent workflow, setup guide and portfolio text.
