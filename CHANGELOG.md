# Changelog

## Unreleased

### Added

- Ingest now decodes each original once and writes an upright working copy (long side at most 1,536 px) and a
  512 px thumbnail; quality, faces and embeddings read the working copy, and the EXIF orientation is applied once.
  Photo IDs are the SHA-256 of the content, so a moved or renamed file keeps its results, an unchanged file is not
  decoded again, and byte-identical copies count as one photo. Only capture time, camera make and model are kept
  from EXIF; GPS is never stored. Tested on synthetic images in the container run (282 engine tests); a full
  `analyze` run and the integration check have not been run. A library analyzed before this change is analyzed
  again from scratch, and its old records are replaced when a file path is seen again.
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
  passes it only through the environment, and downloads a model only with `--fetch-models` (see below). Exit codes
  are 0, 1 (prerequisite), 2 (invalid arguments) and 3 (health check). It is tested with stubbed tools only, not
  against a real Docker daemon. `--codex-login` and `--provider codex` say that they are not available yet.
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
  server and a fake Docker only, not against the real services.
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
- Copies and reports now reach the folder that the output and reports buttons open. The window starts the services
  with an exports folder (`exports` in the app's data folder), the API writes the final set to
  `<exports>/<library id>/output/top_06_final_set`, and the engine writes its reports to
  `<exports>/<library id>/reports`. The folder is created by the app, and one that holds the photo folder or lies inside
  it is refused. If the API cannot write to it (on Linux, a folder not owned by uid 10001), the window says so
  instead of failing silently; see the Linux note in `docs/operations.md`. Deleting a library also removes its copies
  and reports. The window also adds the GPU overlay (`compose.gpu.yaml`) when `nvidia-smi -L` succeeds, as
  `start.sh` does. Tested with a fake Docker, a fake server and synthetic images only; the unwritable-folder case is
  simulated, and nothing was run against a real Docker, GPU or Linux host.
- `./start.sh --rebuild` now also builds the desktop app into `release/` in a container. It uses the
  `electronuserland/builder` images pinned by digest (`:wine` on Windows, `:latest` on Linux) and mounts only the build
  inputs, read-only. `AURAFRAME_BUILDER_IMAGE` can give another pinned image. On the reference Windows PC,
  `./start.sh --rebuild --cpu` exited 0 and wrote the unsigned NSIS installer and `win-unpacked/`. Not yet observed:
  running the built app, and a build on a Linux host.
- `./start.sh` now opens the desktop window once the services are healthy, and waits for it. It uses the app built
  into `release/`, or else the Electron installed by `npm ci` with the output of `npm run build`, and builds the app
  once when neither is there. The window inherits the session token through the environment only, so it shows the
  results of earlier runs at once instead of waiting for a new run. Closing the window, Ctrl+C or a termination
  stops the services exactly once and keeps the data. `npm start` after `./start.sh --backend-only` does the same with
  the token file. If the services later refuse the token (HTTP 401 or 403), the window stops sending requests and
  says that the services are unavailable. Tested with stubbed tools, a fake Docker and a fake server only; the real
  window opened by the script and Ctrl+C in a real terminal have not been observed.
- `bash scripts/check.sh --backend-run` starts the services with `./start.sh --backend-only` on a synthetic folder,
  completes a `fast` run through the API, checks that the photos are listed by ID and the folder is unchanged, then
  stops the services. It passed on the reference PC on CPU, without a window or a local model. It stops services that
  were already running.
- `tests/renderer-isolation.test.cjs` checks the window's trust boundary through the compiled main process and preload:
  no Node, only `window.auraFrame`, a page CSP with no network source, no new windows and no navigation away.
- The engine has a `VisionProvider` contract and a provider for the local llama-server (`engine/src/providers`). It
  compares two photos or critiques one, sends only anonymous 1,024 px JPEG copies (quality 90, no EXIF, name or path),
  and accepts only replies that pass a JSON Schema. Timeouts, retries and call counts are configurable (`ai.local` in
  `engine/config.yaml`) and always clamped. A provider that fails, times out, lacks its model or hits its call limit
  never stops a run: the call comes back as failed and the pair can be decided by the local prescore. The tests use a
  fake server and synthetic images (224 engine tests pass in the container run). It is not wired into the analysis yet
  and no model has been run.
- `./start.sh --fetch-models [model|all]` downloads the local model files, and nothing else downloads them. It runs
  the one-shot `fetch-models` service, the only service with an internet route: it sees the models volume and no
  photos, data or exports, and no plain `up` starts it. A manifest pins every file of the default model and of the
  three bake-off candidates to a repository revision, a size and a SHA-256. A file is kept only when all three match
  and it is GGUF or safetensors; pickle-based formats are refused. A failed download leaves no file under the
  target name. Tested with local fake files (33 new engine tests) and in the isolation check; no model file has
  been downloaded yet.

### Changed

- Settings now has one "Check providers" button. It asks `GET /api/providers` through the bridge (channel
  `providers:check`, method `checkProviders`) and lists each provider with its state, and says so when the
  services are not running. It replaces the earlier local runtime check, and the URL, primary model and fallback
  model fields are gone from Settings and from the saved settings file (old values are dropped on read). The
  engine's `ollama-check` command, its legacy `model:` block in `engine/config.yaml` and the legacy client are
  removed. The engine's model stage now only probes the `ai.local` provider and keeps the local ranking; no photo
  is sent to a model until the pairwise judging of P04. Lint and the build pass, 165 of 166 Node tests pass (1
  skipped) and 225 engine tests pass in the container run, on a fake HTTP server and synthetic images only; no real
  llama-server or model was run.
- The window can no longer navigate to another local file, or in development to a host that only starts like the dev
  server's address. Before, any `file://` URL was allowed. The app itself never navigates, so nothing visible
  changes.

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

- The host Python launcher `start.py` and the Python paths in Electron (`pythonExecutable`, `engineMain`):
  a search of `apps/desktop/electron` finds neither, and the documents now say `start.sh` replaces it.
- All previous documents: the old product specification, architecture, pipeline, design system, cloud
  mode guide, agent workflow, setup guide and portfolio text.
