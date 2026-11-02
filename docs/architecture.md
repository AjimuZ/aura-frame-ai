# Architecture

Status: **target architecture, partly implemented**. The [current state](#current-state-implemented)
and the [migration map](#migration-map) show how the code gets there. The [container stack](#container-stack)
exists, and Electron main's analysis handlers call it over HTTP; see [Bridge mapping](#bridge-mapping) for
what is still open. Decision: [ADR 0009](decisions/0009-electron-desktop-shell.md).

## Overview

```text
Electron window (on the computer)
  Angular renderer ---- sandboxed, no Node, no network
     |  window.auraFrame: typed preload bridge, validated IPC
     v
  Electron main ------- library registry, host folders, native dialogs, OS key store
     |            \
     |             \--> Docker CLI: starts services, mounts the chosen folder
     |  HTTP on 127.0.0.1, per-session token
=====|==================================== Docker ==============================
     v
  gateway ------------- reverse proxy, checks Host
     |                                   networks: frontend, backend
     v
  api ----------------- FastAPI, supervises engine jobs, streams events (SSE)
     |   \                               network: backend (no internet route)
     |    \--> engine jobs: ingest, scoring, grouping, tournament, export
     |
     +--> vision ------ llama-server with the local model on the GPU
     |                                   network: backend (no internet route)
     |
     +--> codex ------- optional sidecar, runs `codex exec`, started only on opt-in
                                         networks: backend, egress
```

**The window is the only part outside the containers.** Photos reach only `api`. The local model
gets resized copies over the internal network. The Codex sidecar gets the same anonymous copies,
and only after opt-in.

## Desktop shell

Most of the Electron layer stays. Its role changes from starting a Python process to being the
only client of the API.

| Responsibility | Status | Notes |
| --- | --- | --- |
| Hardened window, navigation lock, CSP | Stays | `contextIsolation`, `sandbox`, no `nodeIntegration`. Navigation is allowed only to the built `index.html`, or to the dev server's origin in development |
| `window.auraFrame` bridge and IPC channels | Stays | Angular keeps calling the same interface |
| Library registry, per-library host folder | Stays | Host paths stay here. Containers see only `/photos` |
| Native folder and save dialogs, opening folders | Stays | |
| OS key store (`safeStorage`) | Stays | Holds the optional Codex API key |
| Settings file | Stays | Sent to the API with each run |
| `afmedia` scheme for thumbnails | Changes | Proxies the API instead of reading files |
| Analysis handlers | Change | HTTP to the API, instead of spawning Python |
| Python paths (`pythonExecutable`, `engineMain`) | Removed | |

## Services

| Service | Image | Mounts | Network | GPU | Runs |
| --- | --- | --- | --- | --- | --- |
| `gateway` | nginx (unprivileged), reverse proxy only | none | `frontend`, `backend` | no | always |
| `api` | Python 3.12 with PyTorch and CUDA | photos (read-only), data, exports, models (read-only) | `backend` | yes | always |
| `vision` | `ghcr.io/ggml-org/llama.cpp:server-cuda`, pinned by digest | models (read-only) | `backend` | yes | profile `local` (default) |
| `codex` | `node:22-slim`, pinned by digest, with `@openai/codex@0.166.0` | exchange (tmpfs), Codex home | `backend`, `egress` | no | profile `codex` (opt-in) |
| `fetch-models` | `python:3.12-slim`, pinned by digest, standard library only | models (read-write) | `egress` | no | profile `fetch`, one-shot, only on `--fetch-models` |

`backend` is declared `internal: true`, so it has no route to the internet. `gateway` is the only
service that publishes a port, bound to `127.0.0.1`. It exists because a port cannot be published
from an internal network. `egress` has an internet route, and only `fetch-models` and the opt-in `codex` join it.

## Container stack

Written in `compose.yaml` and `docker/` (P02.1), with `gateway`, `api` and `vision`, `fetch-models` since
P03.2 ([Operations](operations.md#model-files)), and the `codex` sidecar since P05.1. Since P05.2.2 its default
command is an HTTP service on port `8090` that `api` calls over `backend` with a per-start token, and only
`./start.sh --provider codex --codex-opt-in` or `--codex-login` starts it; no run sends it comparisons until the
per-library opt-in (P05.2.3). Decisions: [ADR 0010](decisions/0010-compose-stack-layout.md),
[ADR 0011](decisions/0011-pinned-model-download.md), [ADR 0016](decisions/0016-codex-sidecar-image.md),
[ADR 0018](decisions/0018-codex-sidecar-transport.md).

| Rule | Where it is set |
| --- | --- |
| Images pinned by digest | `vision` in `compose.yaml`; the base images in `docker/api/Dockerfile`, `docker/gateway/Dockerfile`, `docker/fetch-models/Dockerfile` and `docker/codex/Dockerfile` |
| Internet route | `fetch-models`, on `egress` only, behind the profile `fetch`, with the `models` volume as its only mount; and `codex`, on exactly `backend` and `egress`, behind the profile `codex`, with the `exchange` tmpfs and the `codex-home` volume as its only mounts. `scripts/compose-policy.cjs` refuses any other service on a routed network other than `gateway` on `frontend` |
| Codex config | `docker/codex/config.toml` (web search, history, analytics, feedback, telemetry and the shell tool off), rewritten into the `codex-home` volume by the entrypoint on every start |
| `api` to `codex` | `docker/codex/sidecar-server.cjs` and `engine/src/providers/codex_sidecar.py`: one authenticated `POST /v1/run` per CLI start, `AURAFRAME_CODEX_TOKEN` passed by compose to these two services only, an allow-list of CLI arguments and files, a private folder in the `exchange` tmpfs per call |
| Model files | Downloaded only by `./start.sh --fetch-models`, each pinned to a commit, a size and a SHA-256 in `engine/src/model_fetch/manifest.json` |
| Non-root, `no-new-privileges`, all capabilities dropped, read-only root filesystem, `/tmp` on tmpfs | Every service, from `x-hardening` in `compose.yaml` |
| GPU access | `compose.gpu.yaml`, an overlay, so the base file starts on a host without a GPU |
| Photos | `AURAFRAME_PHOTOS_DIR`, read-only at `/photos`. Unset means the empty ignored folder `./data/no-photos` |
| Published port | `127.0.0.1:${AURAFRAME_PORT:-8420}` to `gateway:8080` |
| Build context of `api` | `.dockerignore` excludes everything, then allows `engine/` and the health probe, so personal folders never reach the daemon |

`gateway` refuses any Host other than `127.0.0.1` and `localhost` with HTTP 403 and forwards the rest.
The API still checks the Host and the token itself. `gateway` resolves `api` per request, because
recreating `api` for another folder can change its address.

`api` has two build stages. `full`, the default, adds PyTorch with CUDA. `core` leaves that stack out,
with the same package set as the engine tests, and the integration check uses it.

Not observed yet: the `full` stage building, a GPU in a container, `vision` serving a model, the
engine running in `api`, and the `codex` image building or its CLI running. The API writes copies and reports under the `/exports` mount, in a folder per
library (see [Library data layout](#library-data-layout)); no run has written there on a real host yet, and
the GPU overlay choice made by Electron main has not been observed with a real Docker.

## Storage

| Name | Kind | Contents | Why |
| --- | --- | --- | --- |
| photos | bind mount, read-only | The folder chosen in the app, at `/photos` | Originals stay untouched |
| data | named volume | SQLite, thumbnails, working copies, embeddings | SQLite with WAL needs a real Linux filesystem, not a Windows bind mount |
| exports | bind mount | Copies and reports of every library, at `/exports/<library id>/`. `start.sh` and a bare compose use `./data/output` (Git-ignored); Electron main uses `exports/` in its `userData` folder | Electron opens them in the file manager |
| models | named volume | GGUF and PyTorch model files | Survives image rebuilds, and can be mounted read-only |
| exchange | tmpfs | Anonymous copies for the Codex sidecar | Gone when the container stops |
| codex-home | named volume, at `/codex-home` | Codex credentials and config | Visible only to the sidecar; the policy refuses it in any other service |
| registry | Electron `userData` | Library list, host folders, settings, encrypted key | Host-side data, outside the repository |

## GPU scheduling

12 GB cannot hold the scoring models and the judge model together, so they take turns:

1. `api` runs local scoring in a child process, which frees its GPU memory on exit.
2. `vision` loads the judge model on first use and sleeps after 120 idle seconds
   (`--sleep-idle-seconds`).
3. Stages never overlap on the GPU. Memory estimates are in [AI providers](ai-providers.md).

## Runtime flow

1. `./start.sh` checks prerequisites, builds what is missing, and generates a session token.
2. It starts the services, waits for their health checks, and opens the Electron window with the
   token in its environment. The renderer never sees the token.
3. The user picks a folder in the native dialog. Electron main stores the host path in the registry.
4. Before a run, Electron main makes sure `api` has that folder at `/photos`, read-only, by running
   `docker compose up -d --no-deps api` with the folder in the environment. Compose recreates the
   container only if the folder changed. Only one run can exist at a time, so nothing is interrupted.
5. `POST /api/libraries/{id}/runs` starts an engine job as a child process of `api`.
6. The engine prints JSONL events, as it does today. `api` streams them over SSE, and Electron main
   forwards them on the existing `analysis:event` channel.
7. Each stage persists per-photo state in SQLite, so a restart resumes where it stopped.
8. Closing the window stops the services, which frees the GPU. All data stays.

## Bridge mapping

The Angular app reaches the backend through one interface, the `AURA_FRAME_API` token, resolved to
`window.auraFrame`. **That interface stays.** Only the Electron handlers behind it change. The mock used
under `ng serve` stays too.

| Bridge method | Implementation in Electron main (target) |
| --- | --- |
| `getLibraryState`, `createLibrary`, `renameLibrary`, `activateLibrary` | Local registry, as today |
| `deleteLibrary` | Local registry, plus `DELETE /api/libraries/{id}/data` |
| `selectPhotoFolder` | Native dialog, as today. The mount happens before the next run |
| `startAnalysis`, `pauseAnalysis`, `resumeAnalysis`, `cancelAnalysis`, `getAnalysisStatus` | `/api/libraries/{id}/runs` and `/runs/current` |
| `onProgress` | Main consumes `GET /api/libraries/{id}/events` (SSE) and forwards it over IPC |
| `getPhotos` | `GET /api/libraries/{id}/photos` |
| Thumbnails through `afmedia` | Proxied from the API by photo ID, with the token added by main |
| `exportPhoto` | Native save dialog, as today. Copies from the host folder after checking the path stays inside it |
| `exportFinalSet` | `POST /api/libraries/{id}/exports` |
| `openOutputFolder`, `openReportsFolder` | `shell.openPath` on the host folder, as today |
| `getSettings`, `setSettings` | Local settings file, as today |
| `checkProviders` (P03.5; replaces the earlier local-runtime check), `getOpenAiStatus` | One provider check: `GET /api/providers` |
| `getOpenAiKeyStatus`, `setOpenAiKey`, `clearOpenAiKey` | Kept and renamed for the Codex API key, in the OS key store |
| `estimateOpenAiCost`, `startOpenAiQuality`, `exportOpenAiResults` | Removed: the provider setting of a normal run replaces them |
| `clearGeneratedData`, `getReviewSession`, `saveReviewSession` | `/api/libraries/{id}/data`, `/api/libraries/{id}/review` |

Implemented so far (P02.4.2), in `apps/desktop/electron/api-client.ts` and `ipc/analysis.ipc.ts`, and tested
against a fake HTTP server only:

- `startAnalysis`, `pauseAnalysis`, `resumeAnalysis`, `cancelAnalysis`, `getAnalysisStatus`, `onProgress`,
  `getPhotos`, `exportFinalSet`, `clearGeneratedData`, `getReviewSession` and `saveReviewSession` use the routes
  above. Channels are unchanged, and so are the shapes except for `getPhotos` (see the thumbnails item below).
- `startAnalysis` runs `docker compose up -d --wait` with the photo folder and the exports folder in the
  environment (`AURAFRAME_PHOTOS_DIR`, `AURAFRAME_EXPORTS_DIR`; never in the arguments, and not `--no-deps api`),
  and resolves before the run starts. Its outcome arrives as events. Before `up`, main runs `nvidia-smi -L`
  through `spawn` with `shell: false`: when it succeeds, `-f compose.gpu.yaml` joins `-f compose.yaml` (as
  `start.sh` does without `--cpu`), and `down` names the same files. A missing or failing `nvidia-smi` means no
  overlay, not an error. Main creates the exports folder (`exports/` in `userData`) and refuses one that
  contains the photo folder or lies inside it, or that is not an absolute directory.
- `fs:openOutputFolder` and `fs:openReportsFolder` open `<exports>/<library id>/output` and
  `<exports>/<library id>/reports` on the host, which is what the API sees at `/exports/<library id>/output` and
  `/exports/<library id>/reports`. Before the first copy exists, the exports folder opens instead, and no library
  folder is created on the host. `export:photo` offers the library's `output` folder as the save location.
- The client only talks to `127.0.0.1`, adds the token and the Host header, and follows no redirect. Refusals
  become fixed messages, and an answer or event that contains the token is dropped.
- No request leaves main, and so no token, until `stack.start` has resolved true in this session, and none from
  the next start or stop on. Meanwhile reads return their fallbacks (`[]`, `null`, last known status) and writes
  are refused with the fixed "not reachable" message. The gate is in `ApiClient`, the only code that attaches the
  token.
- Inherited token (P02.WINDOW): `takeInheritedSessionToken` reads `AURAFRAME_API_TOKEN` at launch and removes it from
  main's environment; with `AURA_DEV=1` only, it reads `data/session-token` instead. A value that is not 64 lowercase
  hex characters is ignored. With a token, `ComposeStack` uses it instead of generating one and its gate is open from
  launch, so earlier results show without a new run, and a later `up` sends the same token. Without one, main behaves
  as described above. `AURAFRAME_PROJECT_DIR`, from `start.sh`, tells a packaged window where `compose.yaml` is.
- A 401 or 403 from the gateway closes the gate again (`ComposeStack.tokenRefused`), whatever the request (JSON,
  stream or thumbnail). The stack reports a `failed` event with the reason `token-refused`, and main sends the window an
  `error` event with a fixed message saying that the services are unavailable: for a followed run, the run ends with
  it; otherwise it goes to the active library without changing its stored status. Other statuses leave the gate open.
- Closing the window, File > Exit and `app.quit()` stop the services if this session started them
  (`quit-handling.ts`). `down` runs only after an `up` in flight has ended, and a new start is refused until then.
  The wait on quit is bounded to 10 minutes; see [Operations](operations.md#running-the-current-code) for what
  may remain after that. On macOS, closing the last window stops the services and the app stays open.
- Thumbnails (P02.4.3, `media-proxy.ts`): the file-url pipe builds `afmedia://media/?libraryId=<id>&photoId=<id>`.
  Main checks the method, host and path, both ID patterns, that the library is registered, and that no other
  parameter is present, then requests `GET /api/libraries/{id}/media/{photo_id}?variant=thumbnail` through
  `ApiClient`, so the gate above applies. Only a 200 with `image/jpeg`, `image/png` or `image/webp`, at most 16 MiB and
  within 30 seconds, is passed on, with fixed headers. At most 8 media requests run at once, because the gateway has 256
  connections. Everything else, including the time before a start, is the same 404.
- `getPhotos` no longer returns `filePath` or `thumbnailPath`. It returns `hasThumbnail`, so the window can still
  show its "No preview" placeholder. The chosen host folder still reaches the window through the library state, as it
  has to for the folder picker.

Single photo, deletion and refused starts (P02.4.4, `ipc/filesystem.ipc.ts`, `ipc/libraries.ipc.ts`, tested with a
fake server and a temporary folder only):

- `exportPhoto` finds the photo by ID in `GET /api/libraries/{id}/photos`. It maps the stored `/photos/...` path to the
  library's chosen host folder, and refuses a path that is not under `/photos`, holds a `.`, `..`, empty or backslash
  segment, or whose real path (links included) is outside that folder. The original is copied through the native save
  dialog, and a destination equal to the original is still refused. Messages are fixed: no reply or error carries a
  path or the token.
- `deleteLibrary` calls `DELETE /api/libraries/{id}/data` first and removes the registry entry only when it succeeds.
  If the API fails, the library stays. The last library is still refused.
- While the services are not up in this session (`stack.isUp()`), `exportPhoto` and `deleteLibrary` send nothing. The
  export says the services are not running. The deletion is refused with a fixed message that tells the person to start
  an analysis first, so a library's data is never left in the volume without an owner. A consequence: right after
  launch, a library cannot be deleted until a run has started the services, unless the window inherited a token.
- A start refused because a run is still active (a cancel finishing included) or the services are stopping now also
  sends an `error` event for that library, so the window leaves "Starting..." without a refresh. The refusal does not
  change the stored status of the run that caused it, and the call still rejects with the same message.

Provider check (P03.5, tested with a fake HTTP server only): `checkProviders` (channel `providers:check`) asks
`GET /api/providers` through `ApiClient`, so the gate above applies. It never rejects: it answers
`{ reachable, providers, message }`, with `reachable: false`, an empty list and a fixed message while the services are
down, refused, or send an unusable answer. Settings shows each provider's label, state and detail. The earlier local
runtime check, its URL and model-name settings, its engine command and its legacy engine config keys are gone; the
engine's model stage only probes the `ai.local` provider and keeps the local ranking (pairwise judging arrives with
P04). The API still reports provider configuration only, and does not contact them.

Not done yet: `getOpenAiStatus` still returns its fixed "unable to probe" answer (P05.5). `estimateOpenAiCost`,
`startOpenAiQuality` and `exportOpenAiResults` keep their channels but do nothing (P05.5).

## Library data layout

Written in `engine/api/storage.py`; the routes for photos, media, providers, data and review exist
(P02.3.2), and so do those for runs, events and exports ([below](#runs-events-and-exports)). The
`api` container serves them, and Electron main calls them ([Bridge mapping](#bridge-mapping)).

| Setting | Default | Meaning |
| --- | --- | --- |
| `AURAFRAME_PHOTOS_ROOT` | `/photos` | The chosen folder, mounted read-only |
| `AURAFRAME_DATA_ROOT` | `/data` | The writable volume for generated data |
| `AURAFRAME_EXPORTS_ROOT` | `/exports` | The writable mount of the folder the person opens |

Each library keeps its working data under `<data root>/libraries/<library id>/`, using the engine's own
`--data-root` layout: `working/auraframe.db` and `working/thumbs/`. Its copies and reports go under
`<exports root>/<library id>/`: `output/` (with `top_06_final_set/` for the exported final set) and `reports/`,
which is the engine's `--exports-root`. The three roots may not contain one another, so clearing data cannot
reach an original and an export cannot land in the photos folder.

Rules the routes follow:

- A library id matches `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` and a photo id matches `^(?:[0-9a-f]{64}|IMG_[0-9A-F]{10})$`: the SHA-256 of the content, or the older path-based shape, which is still accepted so a library analyzed before content IDs can be listed until it is analyzed again.
  Anything else is refused with HTTP 422. No route takes a path.
- `GET /api/libraries/{id}/media/{photo_id}` looks the photo up in SQLite. The stored path must
  resolve inside the photos root (`?variant=original`) or the library's `thumbs/` directory (default
  `?variant=thumbnail`), else the answer is 404 `media_unavailable`.
- `DELETE /api/libraries/{id}/data` removes `working` (plus any `output` and `reports` left in the library's
  data folder by earlier versions) and the library's `output` and `reports` under the exports root. It
  is idempotent and does not follow symlinks. It never touches the photos root. A folder it may not remove
  is a 409 `data_folder_unwritable`.
- `GET /api/libraries/{id}/photos` returns the stored camelCase records, or `[]` for a library with no
  database yet. `GET` and `PUT /api/libraries/{id}/review` use the `slotAssignments`, `locks` and
  `shortlist` shape of the bridge, and `PUT` rejects unknown fields and oversized bodies.
- `GET /api/providers` reports configuration only, with no probe: local scoring is `available`, the
  local vision model is `not_configured` and Codex is `disabled`. The real contract arrives with P03.

## Runs, events and exports

Written in `engine/api/runs.py` and `engine/api/exports.py` (P02.3.3), and served by the `api` container.
Their tests (stub engine, synthetic images) pass in the container pytest run; the routes have not been run
against the real engine.

| Route | Behavior |
| --- | --- |
| `POST /api/libraries/{id}/runs` | Starts the engine and answers 202 with the status. 409 `run_in_progress` while any run is active, 409 `photos_unavailable` without a mounted folder, 503 `engine_unavailable` when the process cannot start |
| `GET /api/libraries/{id}/runs/current` | The latest run of that library in the `AnalysisStatus` shape, or an idle status |
| `POST /api/libraries/{id}/runs/current/pause`, `/resume`, `/cancel` | Write the engine's `working/control.json`. 409 `no_active_run` when that library has no running analysis |
| `GET /api/libraries/{id}/events` | Server-sent events: the run's buffered JSONL events, then live ones. The stream ends with the run. 404 `no_run` before any run |
| `POST /api/libraries/{id}/exports` | Body `{"ids": [...]}`. Copies the originals into `/exports/<library id>/output/top_06_final_set/NN_<photo id>.<ext>` and answers `{"exported": n}` |

Rules these routes follow:

- A run body holds options only: `runGemma`, `preset` (`fast`, `balanced`, `quality`) and
  `includeSubfolders`. There is no folder field, so a run always analyzes the mounted photos root, and
  unknown fields are refused with HTTP 422. The engine command is the fixed `ApiSettings.engine_command`
  plus arguments built on the server; the process starts without a shell and without the API token.
  `runGemma` still reaches the legacy `--gemma` flag until package P03 replaces that review.
- One run exists at a time across all libraries. Cancel gives the engine 30 seconds to stop on its own,
  then kills it. A cancel left in `control.json` by an earlier run is cleared when a new run starts.
- Events are the engine's JSONL lines. Lines that are not JSON objects with a lowercase `type`, or that
  exceed 4096 bytes, are dropped. The photos, data and exports root paths are replaced by `<photos>`,
  `<data>` and `<exports>`, and each event gets the `libraryId`. At most 10000 events are kept per run, oldest first
  dropped. If the engine exits without a `done` or `error` event, the API adds one. The engine's stderr
  is not forwarded; it goes to the API container log.
- Exports are validated before anything is copied: ids match the photo id pattern (1 to 500), are known
  to the library, and point to an image file inside the photos root. Otherwise HTTP 422 and no file is
  written. A symlinked library folder or output folder is refused with 409 `export_folder_unsafe`, and a
  symlinked destination file is replaced, never followed. Originals are only read.
- The exports mount must be writable by the API user (uid 10001). The route checks that before it copies and
  the run route checks it before it starts the engine, so a folder the API cannot write to gives
  409 `export_folder_unwritable` with a fixed message, not a failed run or a silent empty folder.
  The run passes `--exports-root` so the engine writes its reports under the same library folder.

## Target repository layout

```text
start.sh                 Entry point
compose.yaml             Services, networks, volumes
compose.gpu.yaml         GPU access for api and vision, as an overlay
docker/                  Dockerfiles, gateway configuration, desktop shell build
apps/desktop/angular/    Angular renderer
apps/desktop/electron/   Electron main and preload, now a client of the API
engine/                  Python package: pipeline, providers, CLI
engine/api/              FastAPI application
engine/tests/            Tests with synthetic images
docs/                    This documentation
```

## Current state (implemented)

Verified on 2026-11-01. Details and defects are in the [assessment](assessment.md).

```text
Angular UI -> Electron renderer -> typed preload bridge -> Electron main process
  -> docker compose up with the chosen folder, then HTTP with the session token -> gateway -> api
  -> Python engine as a child process of api (JSONL to SSE, one SQLite per library)
  -> OpenCV and MediaPipe measurements -> OpenCLIP and LAION aesthetic score on CPU
  -> model stage: a health probe of the local llama-server provider (P03.5); no photo is sent to a model yet
  -> optional OpenAI API flow in engine/src/cloud/, which the window can no longer start
```

Electron main's side of this path has been tested with a fake server and a fake Docker only
([Bridge mapping](#bridge-mapping)).

## Migration map

| Today | Target | Package |
| --- | --- | --- |
| Electron | Done in P01: 45.2.1, a supported major version | P01 |
| `apps/desktop/electron/ipc/library-store.ts` | Done in P01: libraries, without the legacy migration and hard-coded name | P01 |
| `apps/desktop/electron/ipc/analysis.ipc.ts` | Done in P02.4.2: same channels, HTTP to the API instead of a child process | P02 |
| `apps/desktop/electron/ipc/paths.ts` | Done in P02.4.2: Python paths removed | P02 |
| `apps/desktop/electron/main.ts`, `filesystem.ipc.ts`, `settings.ipc.ts`, `preload.ts` | Stay, with small changes | P02 |
| `apps/desktop/angular/` | Stays. New design in P06 | P06 |
| `start.py` | `start.sh` | Done in P02.6: `start.py` is absent |
| The earlier local runtime client in `engine/src/vlm/` | Done in P03.5: removed. `engine/src/providers/llama_server.py` (P03.1) is the local provider, and `vlm/runner.py` only probes it. `gemma_judge.py` stays as a facade until P04 | P03 |
| `engine/src/quality_score.py`, `aesthetic.py` | Q-ReAlign scorer | P04 |
| `engine/src/embeddings.py` | SigLIP 2 embeddings with grouping | P04 |
| `engine/src/face_quality.py` | MediaPipe Face Landmarker signals | P04 |
| `engine/src/local_gate.py`, `final_ranker.py` | Sanity gate, tournament and selection | P04 |
| `engine/src/ingest.py`, `db.py`, `progress_events.py` | Kept and extended | P04 |
| `engine/src/generate_report.py`, `export_results.py` | Kept, simplified | P04 |
| `engine/src/cloud/` | Removed. Reusable parts move to `providers/` | P05 |
