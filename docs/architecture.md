# Architecture

Status: **target architecture, not implemented**. The [current state](#current-state-implemented)
and the [migration map](#migration-map) show how the code gets there. Decision:
[ADR 0009](decisions/0009-electron-desktop-shell.md).

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
| Hardened window, navigation lock, CSP | Stays | `contextIsolation`, `sandbox`, no `nodeIntegration` |
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
| `codex` | Node with `@openai/codex`, pinned version | exchange (tmpfs), Codex home | `backend`, `egress` | no | profile `codex` (opt-in) |
| `fetch-models` | small Python image | models (read-write) | `egress` | no | one-shot, on `--fetch-models` |

`backend` is declared `internal: true`, so it has no route to the internet. `gateway` is the only
service that publishes a port, bound to `127.0.0.1`. It exists because a port cannot be published
from an internal network.

## Storage

| Name | Kind | Contents | Why |
| --- | --- | --- | --- |
| photos | bind mount, read-only | The folder chosen in the app, at `/photos` | Originals stay untouched |
| data | named volume | SQLite, thumbnails, working copies, embeddings | SQLite with WAL needs a real Linux filesystem, not a Windows bind mount |
| exports | bind mount | Copies and reports, at `./data/output` (Git-ignored) | Electron opens them in the file manager |
| models | named volume | GGUF and PyTorch model files | Survives image rebuilds, and can be mounted read-only |
| exchange | tmpfs | Anonymous copies for the Codex sidecar | Gone when the container stops |
| codex-home | named volume | Codex credentials and config | Visible only to the sidecar |
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
| `checkOllama`, `getOpenAiStatus` | Replaced by one provider check: `GET /api/providers` |
| `getOpenAiKeyStatus`, `setOpenAiKey`, `clearOpenAiKey` | Kept and renamed for the Codex API key, in the OS key store |
| `estimateOpenAiCost`, `startOpenAiQuality`, `exportOpenAiResults` | Removed: the provider setting of a normal run replaces them |
| `clearGeneratedData`, `getReviewSession`, `saveReviewSession` | `/api/libraries/{id}/data`, `/api/libraries/{id}/review` |

## Target repository layout

```text
start.sh                 Entry point
compose.yaml             Services, networks, volumes
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
  -> Python engine as a child process (JSONL on stdout, one SQLite per library)
  -> OpenCV and MediaPipe measurements -> OpenCLIP and LAION aesthetic score on CPU
  -> Ollama HTTP client for local AI review
  -> optional OpenAI API flow in engine/src/cloud/
```

## Migration map

| Today | Target | Package |
| --- | --- | --- |
| Electron | Done in P01: 45.2.1, a supported major version | P01 |
| `apps/desktop/electron/ipc/library-store.ts` | Done in P01: libraries, without the legacy migration and hard-coded name | P01 |
| `apps/desktop/electron/ipc/analysis.ipc.ts` | Same channels, HTTP to the API instead of a child process | P02 |
| `apps/desktop/electron/ipc/paths.ts` | Python paths removed | P02 |
| `apps/desktop/electron/main.ts`, `filesystem.ipc.ts`, `settings.ipc.ts`, `preload.ts` | Stay, with small changes | P02 |
| `apps/desktop/angular/` | Stays. New design in P06 | P06 |
| `start.py` | `start.sh` | P02 |
| `engine/src/vlm/ollama_client.py`, `gemma_judge.py` | `engine/src/providers/local_llama.py` | P03 |
| `engine/src/quality_score.py`, `aesthetic.py` | Q-ReAlign scorer | P04 |
| `engine/src/embeddings.py` | SigLIP 2 embeddings with grouping | P04 |
| `engine/src/face_quality.py` | MediaPipe Face Landmarker signals | P04 |
| `engine/src/local_gate.py`, `final_ranker.py` | Sanity gate, tournament and selection | P04 |
| `engine/src/ingest.py`, `db.py`, `progress_events.py` | Kept and extended | P04 |
| `engine/src/generate_report.py`, `export_results.py` | Kept, simplified | P04 |
| `engine/src/cloud/` | Removed. Reusable parts move to `providers/` | P05 |
