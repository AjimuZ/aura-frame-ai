# Roadmap

The order below is a proposal. **The owner decides when and how each package starts.** No package
after P00 has started. An item is checked only after its exit checks pass.

```text
P00 -> P01 -> P02 -> P03 -> P04 -> P05
                 \            \--> P06
                  \----------------^
P04, P05, P06 -> P07
```

## 1. P00 — Baseline and documentation

**Depends on:** nothing. **Areas:** documentation, tooling.

- [x] P00.1 Assess the repository, with evidence.
- [x] P00.2 Research the current state of the art for the pipeline, local models and Codex CLI.
- [x] P00.3 Replace all documentation with the new set.
- [x] P00.4 Review and install 10 Claude Code skills at user scope.
- [x] P00.5 Make the first commit. The owner must authorize this.

Exit: documentation describes the target and marks what is planned; no retired term remains in the
documentation; the first commit exists.

P00.1–P00.4 evidence (2026-11-01): see [assessment](assessment.md), [testing](testing.md) and
[Claude Code](claude-code.md). P00.5 remains open.

## 2. P01 — Foundation

**Depends on:** P00. **Areas:** repository, naming, checks.

- [x] P01.1 Add `scripts/check.sh` as the single entry point for all checks.
- [x] P01.2 Remove the retired product vocabulary from code, prompts, schemas, names, comments,
      tests and `package.json`. Rename `profile` to `library` and the AI fields to neutral names.
- [x] P01.3 Remove the hard-coded personal name from code, mock data and tests.
- [ ] P01.4 Add the `LICENSE` file for the declared license, or change the declaration.
- [x] P01.5 Remove the broken `.venv`, and run Python only in containers.
- [x] P01.6 Upgrade Electron and `electron-builder` to supported versions.

Exit: `scripts/check.sh` passes; the retired-terms test passes; no user path or personal name in
tracked files; the desktop app starts on a supported Electron version.

Evidence 2026-11-01: `bash scripts/check.sh` ran all five steps and passed (lint, Angular build,
Electron build, Node tests 14 of 14, engine pytest 28 of 28 in a container). The Node tests include
the retired-terms test and the personal-data test. Not observed: the desktop app starting
(P01.SMOKE) and P01.4 (`LICENSE`), which is still open.

## 3. P02 — Container stack

**Depends on:** P01. **Areas:** Docker, API, Electron main.

- [x] P02.1 Add `compose.yaml`, Dockerfiles, networks, volumes and hardening.
- [x] P02.2 Add `start.sh` with the contract in [Operations](operations.md#startsh).
- [x] P02.3 Add the FastAPI application: runs, events, photos, media, exports, data.
- [x] P02.4 Make Electron main a client of the API: same bridge and IPC channels, HTTP in place of
      the child process, `afmedia` proxied, chosen folder mounted read-only.
- [ ] P02.5 Build the desktop shell in a container, or record why the host fallback is needed.
- [ ] P02.6 Remove `start.py` and the Python paths from Electron.

Exit: `./start.sh` opens the Electron window; a synthetic folder chosen in the native dialog completes
a `fast` run end to end; closing the window stops the services; the network, read-only, token and
hardening checks in [Testing](testing.md) pass.

Progress 2026-11-01: P02.3.1 added the `engine/api` skeleton (app factory, session-token and Host guard,
no CORS, `/health`, shared Pydantic models) and pinned `fastapi`, `pydantic`, `uvicorn` and `httpx` in
`engine/requirements.txt`. Its tests are in `engine/tests/test_api_guard.py`. The runs, events, photos,
media, exports and data routes are not written yet, and the API is not wired into a container.

Progress 2026-11-01: P02.3.2 added the photos, media (by photo ID), providers, data (DELETE) and review
(GET, PUT) routes in `engine/api/routes.py`, with the data layout in `engine/api/storage.py`
([Architecture](architecture.md#library-data-layout)). Tests are in `engine/tests/test_api_routes.py`.
The runs, events and exports routes followed in P02.3.3 (below).

Progress 2026-11-01: P02.3.3 added the runs, `runs/current` (status, pause, resume, cancel), events (SSE)
and exports routes, with the engine supervisor in `engine/api/runs.py` and the copy-only export in
`engine/api/exports.py` ([Architecture](architecture.md#runs-events-and-exports)). Tests are in
`engine/tests/test_api_runs.py`, with a stub engine command and synthetic images. Observed:
`bash scripts/check.sh --engine` (the container pytest run, without torch) passed with 182 tests and 1
warning, which covers the P02.3.1, P02.3.2 and P02.3.3 suites and the new dependency pins. Not observed:
the routes against the real engine pipeline. P02.1 wired the API into a container (below).

Progress 2026-11-01: P02.1 added `compose.yaml` with `gateway`, `api` and `vision`, the GPU overlay
`compose.gpu.yaml`, the Dockerfiles and gateway configuration in `docker/`, and `.dockerignore`
([Architecture](architecture.md#container-stack), [ADR 0010](decisions/0010-compose-stack-layout.md)).
Observed: `bash scripts/check.sh --compose` passed, and `bash scripts/check.sh --integration` passed with
all assertions holding ([Testing](testing.md#container-isolation)). Not observed: the `full` stage of `api`
building, a GPU in a container, `vision` serving a model, and the engine running in `api`. Debian packages
and the transitive Python packages in the `api` image are not pinned, only its base image and the direct
Python requirements.

Progress 2026-11-01: P02.2 added `start.sh` ([Operations](operations.md#startsh)) and
`tests/start-sh.test.cjs`, which runs it through bash with stubbed `docker`, `nvidia-smi`, `curl` and `uname`.
Observed: `bash scripts/check.sh --node` passed (lint, both builds, `bash -n start.sh`, and 49 Node tests, 11 of
them for `start.sh`: exit codes 2, 1 and 3, the token absent from every argument, the model guidance,
`MSYS_NO_PATHCONV=1` and the memory warning). Not observed: `start.sh` against a real Docker daemon, so the
`compose run` listing of the models volume and `up --wait` are verified only through stubs. Not built yet:
the window (P02.4, P02.5), `--fetch-models` (P03.2), `--codex-login` and `--provider codex` (P05.2); those
flags print "not available yet" and exit 1. The file needs its executable bit in Git
(`git update-index --chmod=+x start.sh`), which this session could not set.

Progress 2026-11-01: P02.4.1 added `apps/desktop/electron/compose-stack.ts` and `tests/compose-stack.test.cjs`.
The module validates the chosen folder (absolute, existing, a directory, symlinks resolved), runs
`docker compose -p auraframe ... up -d --wait` and `down` through `spawn` with `shell: false`, and passes the
folder and the per-session token only in the child environment (`AURAFRAME_PHOTOS_DIR`,
`AURAFRAME_API_TOKEN`). The token (32 random bytes, hex) is generated in main, has no getter other than
`authorizationHeader()` for main's own HTTP client, and appears in no event or return value. Observed: lint,
`build:electron` and the Node tests pass (57 passed, 1 skipped; the skipped one is the symlink test, because
this Windows session may not create symlinks). Not observed: a real Docker daemon. Not built yet: the IPC
wiring, the HTTP client and the `afmedia` proxy (P02.4.2, P02.4.3); the module is not called from `main.ts`
yet, so P02.4 stays unchecked.

Progress 2026-11-01: P02.4.2 added `apps/desktop/electron/api-client.ts` and `tests/analysis-http.test.cjs`, and
`ipc/analysis.ipc.ts` now calls the API over HTTP (runs, `runs/current`, SSE events, photos, review, data, exports)
with the same channels. `main.ts` builds the `ComposeStack` and the client; `analysis:start` starts the stack with
the chosen folder. The token and Host header are added in main only, and no request leaves main until
`stack.start` has resolved true in this session. `quit-handling.ts` makes closing the window, File > Exit and
`app.quit()` stop the services, with `down` only after an `up` in flight has ended, and bounds that wait to 10
minutes. The API has no route for the legacy local-model and cloud handlers, so they return their fallbacks and
contact nothing. Details: [Bridge mapping](architecture.md#bridge-mapping). Observed: `bash scripts/check.sh --node`
passed (lint, both builds, `bash -n start.sh`, and 96 Node tests: 95 passed, 1 skipped, the symlink case). Not
observed: the client against the real API, gateway, Docker or a running Electron, including the quit paths and the
dialog. Still open in P02.4: the `afmedia` proxy (P02.4.3), `export:photo` (it reads a host `photos.json`),
`DELETE .../data` when a library is deleted, connecting `/exports` to the API's export folder, `compose.gpu.yaml`
in the stack module, which runs without a GPU, and the status left at "Starting..." when a restart is refused
during a cancel ([Operations](operations.md#running-the-current-code)). So P02.4 stays unchecked.

Progress 2026-11-01: P02.4.3 added `apps/desktop/electron/media-proxy.ts` and `tests/media-proxy.test.cjs`. The
`afmedia` handler in `main.ts` now asks the API for a thumbnail by photo ID through `ApiClient.getThumbnail`, behind
the same gate as every other request, and the file-url pipe builds its URL from IDs. `photos:get` drops `filePath`
and `thumbnailPath` and adds `hasThumbnail`. Details: [Bridge mapping](architecture.md#bridge-mapping). Observed:
`bash scripts/check.sh --node` passed (lint, both builds, `bash -n start.sh`, and 107 Node tests: 106 passed, 1
skipped, the symlink case). Not run: the engine container tests (no engine file changed), `--compose` and
`--integration`. Not observed: the proxy against the real API or gateway, or an image displayed in a running Electron.
These items are still open in P02.4 and are not delivered: `export:photo`, `DELETE .../data` when a library is deleted,
and the status left at "Starting..." when a restart is refused during a cancel (P02.4.4); connecting `/exports` to the
API's export folder and `compose.gpu.yaml` in the stack module (P02.4.5). Showing earlier results before a run and the
token from `start.sh` belong to P02.WINDOW. So P02.4 stays unchecked until P02.4.5 passes.

Progress 2026-11-02: P02.4.4 changed `ipc/filesystem.ipc.ts`, `ipc/libraries.ipc.ts`, `ipc/analysis.ipc.ts`,
`api-client.ts` and `main.ts`, and added `tests/bridge-handlers.test.cjs`. `export:photo` finds the photo by ID through
`ApiClient`, maps its stored path to the library's host folder with a real-path check, and keeps the check that refuses
a destination equal to the original. `libraries:delete` calls `DELETE .../data` before the registry entry goes, and
both handlers send nothing while the services are not up (the deletion is refused with a fixed message). A refused
`analysis:start` now also sends an error event, so the window leaves "Starting...". Details:
[Bridge mapping](architecture.md#bridge-mapping). Observed: `bash scripts/check.sh --node` passed (lint, both builds,
`bash -n start.sh`, and 118 Node tests: 117 passed, 1 skipped, the symlink case of `compose-stack`). The link-escape
test ran with a directory junction. Not run: the engine container tests (no engine file changed), `--compose` and
`--integration`. Not observed: these handlers against the real API or in a running Electron, including the native save
dialog. Still open in P02.4: `/exports` and `compose.gpu.yaml` (P02.4.5).

Progress 2026-11-02 (later): P02.4.5 connected the `/exports` mount. `ComposeStack` takes an exports folder, creates and
validates it in main (absolute, a directory, apart from the photo folder), and passes it only as
`AURAFRAME_EXPORTS_DIR`. The API writes copies to `/exports/<library id>/output/top_06_final_set`, and the run passes
the engine `--exports-root` so reports land in `/exports/<library id>/reports`. `fs:openOutputFolder` and
`fs:openReportsFolder` open the matching host folders. `ComposeStack` probes `nvidia-smi -L` (spawn, `shell: false`) and
adds `-f compose.gpu.yaml` on success, and `down` names the same files. A folder the API cannot write gives 409
`export_folder_unwritable`. Details: [Bridge mapping](architecture.md#bridge-mapping),
[Library data layout](architecture.md#library-data-layout), [Operations](operations.md#compose-stack). Observed: lint,
`build:electron`, `build:angular` and `bash scripts/check.sh --compose` passed; the Node tests passed (130: 129 passed, 1
skipped, the symlink case of `compose-stack`); the engine container tests passed (190 of 190). Not observed: the
unwritable case with a real uid 10001 on a Linux host (simulated in the tests), a real Docker, `nvidia-smi` or GPU
overlay, and a running Electron. `--integration` was not run. P02.4 is left for the orchestrator to tick.

## 4. P03 — Local AI runtime

**Depends on:** P02. **Areas:** providers, models.

- [ ] P03.1 Define the `VisionProvider` contract and implement the llama-server provider.
- [ ] P03.2 Add `fetch-models` with pinned revisions and SHA-256 checks.
- [ ] P03.3 Run the [bake-off](ai-providers.md#bake-off) on the reference PC, and record the results.
- [ ] P03.4 Confirm or replace the default in [ADR 0004](decisions/0004-default-local-model.md).
- [ ] P03.5 Remove the Ollama client, its settings, UI texts and tests.

Exit: the default model completes the benchmark on the reference PC with at least 0.5 GiB of GPU
memory free at peak; no Ollama reference remains in the code; provider contract tests pass.

## 5. P04 — Curation pipeline

**Depends on:** P03. **Areas:** engine, database, reports.

- [ ] P04.1 New ingest: content-hash IDs, working copies, one decode, orientation applied once.
- [ ] P04.2 Sanity gate that rejects only objective failures.
- [ ] P04.3 Q-ReAlign scoring and SigLIP 2 embeddings, batched on the GPU.
- [ ] P04.4 Order-independent grouping, content tags and people signals with no identity.
- [ ] P04.5 Shortlist, Swiss tournament in both orders, Bradley-Terry rank, verdict cache.
- [ ] P04.6 Critique, diversity-aware selection, tiers, reports and exports.
- [ ] P04.7 Persist per-stage progress so resume really works.
- [ ] P04.8 Remove the old gate, ranker, slots, OpenCLIP and LAION paths.

Exit: the regression corpus in [Testing](testing.md#regression-corpus-p04) passes; a run on 2,000
synthetic images completes on the reference PC with recorded time and peak memory; the benchmark
score of the pipeline is recorded and is not below the scorer alone.

## 6. P05 — Codex CLI provider

**Depends on:** P03. **Areas:** providers, security.

- [ ] P05.1 Build the sidecar image, its config with tools and telemetry off, and its volumes.
- [ ] P05.2 Implement the provider, opt-in, call cap and `--codex-login`.
- [ ] P05.3 Check the upstream sandbox in Docker, and record the outcome.
- [ ] P05.4 Measure the usage cost per comparison, and record it.
- [ ] P05.5 Remove `engine/src/cloud/`, after moving the image preparer into `providers/`
      ([ADR 0007](decisions/0007-retire-openai-quality-mode.md)).

Exit: the contract and data-shape tests pass; without opt-in the sidecar is not running and no call
can happen; a real run with the owner's account completes on synthetic images.

## 7. P06 — Interface

**Depends on:** P02; screens for new data depend on P04. **Areas:** web.

- [ ] P06.1 Add non-color tokens and shared primitives on the existing palette.
- [ ] P06.2 Apply them to layout, navigation and every screen.
- [ ] P06.3 Add the pixel icon sprite and, if approved, a pixel title font.
- [ ] P06.4 Build the review flow: alternates, A/B compare, stored decisions.

Exit: all checks in [Design system](design-system.md#verification) pass; the end-to-end test passes.

## 8. P07 — Release

**Depends on:** P04, P05, P06. **Areas:** documentation, release.

- [ ] P07.1 Remove the "planned" marks from what was delivered, and fix what changed.
- [ ] P07.2 Add a project summary for a portfolio, written only from delivered behavior.
- [ ] P07.3 Add real screenshots made from synthetic photos.
- [ ] P07.4 Tag version 2.0.0, after authorization by the owner.

Exit: a clean clone runs on the reference PC by following the documentation alone.

## Later

- Taste model trained on the user's own A/B choices.
- Heavier scorers (Q-ReAlign Lite or Pro) on larger GPUs.
- AMD and Intel GPUs through the llama.cpp Vulkan image.
