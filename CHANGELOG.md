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
  GPU access and a served model have not been run.
- `start.sh` starts, inspects and stops the container stack: `--cpu`, `--rebuild`, `--backend-only`,
  `--provider local|none`, `--status`, `--logs [service]` and `--stop`. It checks Docker, Compose, the GPU
  and the model files, warns when Docker has less than 10 GB, generates a new session token per start and
  passes it only through the environment, and never downloads a model. Exit codes are 0, 1 (prerequisite),
  2 (invalid arguments) and 3 (health check). It is tested with stubbed tools only, not against a real
  Docker daemon. It does not open the desktop window yet, and `--fetch-models`, `--codex-login` and
  `--provider codex` say that they are not available yet.
- Electron main has a module that starts and stops the container stack for a chosen photo folder. It checks
  that the folder is an existing directory, runs Docker without a shell, and passes the folder and a fresh
  per-session token only through the environment, so neither appears in a command line, event or reply.
  It has been tested with a fake Docker only.
- The desktop app now talks to the containerised engine. Starting an analysis brings the services up with the
  chosen folder mounted read-only and starts the run through the API. Progress, pause, resume, cancel, the
  photo list, the review session, clearing data and exporting the final set also use the API, with the same
  window behavior and channels. Only Electron main holds the session token, no reply or event carries it, and it
  is sent to no one until this session has started the services. Closing the window or quitting the app stops
  the services it started and keeps the data, after waiting for a start in progress. If Docker has not finished
  within 10 minutes, a dialog says what may still be running and the app exits. This has been tested with a fake
  server and a fake Docker only, not against the real services. Still open: showing earlier results before a run
  starts the services. The final set is copied into the data volume, so the output folder button does not show it yet.
- Thumbnails now come from the containerised engine. The window asks for a thumbnail by photo ID, and Electron main
  fetches it from the API with the session token, so the window holds no photo path and no token. Until this
  session has started the services, nothing is sent and the thumbnail counts as missing. Originals cannot be
  requested this way. This has been tested with a fake server only, not against the real services or in a running
  window.
- Exporting a single photo now finds the photo through the API, maps its stored path to the library's photo folder
  and refuses a path that leaves that folder. Deleting a library now removes its stored data from the volume first,
  and is refused with a message while the services are not running, so no data is left behind. Neither sends anything
  to the API before this session has started the services. A start refused because a run is still finishing now shows
  the refusal in the window instead of staying at "Starting...". Tested with a fake server and a temporary folder only,
  not in a running window.

### Changed

- Electron moves from 33.4.11, which was out of support, to 45.2.1, and `electron-builder` from 25.1.8 to
  26.18.0. Working on the app now needs Node.js 22.12 or newer, and the first `electron` run downloads the
  binary. The window hardening, navigation lock, CSP and `afmedia` scheme are unchanged. The upgraded app
  passes build, lint and tests but has not yet been started.

- Python now runs only in containers. The `engine:*` npm scripts that called Python on the host are
  removed, and the Electron main process no longer looks for a host interpreter or a `.venv`.
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
