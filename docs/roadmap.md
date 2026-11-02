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
- [x] P02.5 Build the desktop shell in a container, or record why the host fallback is needed.
- [x] P02.6 Remove `start.py` and the Python paths from Electron.

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
flags printed "not available yet" and exited 1 (`--fetch-models` works since P03.2). The file needs its executable bit in Git
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

Progress 2026-11-02 (P02.5): `start.sh --rebuild` builds the desktop shell in a digest-pinned image, with only the
build inputs mounted ([Operations](operations.md#desktop-shell-build)). With the owner's authorization, both builder
tags were pulled and their digests read with `docker inspect` and set in `start.sh`. Observed on the reference PC:
`./start.sh --rebuild --cpu` exited 0, wrote `release/AuraFrame AI Setup 1.0.0.exe` and `release/win-unpacked/` from
the `:wine` image, then started the services and the gateway answered `/health`. An earlier `./start.sh --rebuild` with
the GPU overlay wrote the same files, but the session's 10-minute command limit stopped the script before its exit
code. R15 records the outcome, and no host fallback is needed on this PC. `node --test tests/start-sh.test.cjs` passed.
Not observed: running the built app, and the Linux route.

Progress 2026-11-02 (P02.WINDOW): `./start.sh` without `--backend-only` finds the window before starting the services
(the shell in `release/`, else the host fallback, else one shell build, else exit 1), runs it in the foreground with
the token in its environment only, and runs `down` once when it closes or on SIGINT, SIGTERM or SIGHUP, keeping the
volumes. Electron main adopts `AURAFRAME_API_TOKEN`, or `data/session-token` with `AURA_DEV=1`, when it is 64 lowercase
hex characters, so its gate is open from launch; a 401 or 403 closes the gate and the window reports that the services
are unavailable. Details: [Operations](operations.md#startsh), [Bridge mapping](architecture.md#bridge-mapping).
Observed: `bash scripts/check.sh` passed (8 of 8: lint, both builds, `start.sh` syntax, 156 Node tests with 155 passed
and 1 skipped, the symlink case, the engine container tests 190 of 190, and both compose checks). Not observed: the
real window opened by the script, a `fast` run from it, Ctrl+C in a real terminal, and the Linux shell path.

Evidence 2026-11-02 (P02.EXIT), on the reference PC: `bash scripts/check.sh --integration` passed (3 of 3; the
network, read-only, port, Docker socket, token and Host assertions all hold). The new `tests/renderer-isolation.test.cjs`
(8 of 8, no new dependency) checks the window options, the single `window.auraFrame` bridge of functions, a CSP with no
network source, denied new windows and refused navigations, through the compiled main process and preload. It led to a
tighter navigation lock: only the built `index.html`, or the dev server's origin in development, where any `file://` URL
and look-alike hosts used to pass. `bash scripts/check.sh --backend-run` passed: `./start.sh --backend-only --cpu
--provider none` on 6 synthetic images, a `fast` run through the API ending with step `done` (6 of 6), the photos listed
by ID and the folder unchanged; it then stopped the services. The hook rules were reviewed as
[Claude Code](claude-code.md#hook-review-after-the-container-move-p02-2026-11-02) asks: nothing changed, and six
proposals are left for the owner. `bash scripts/check.sh` passed (8 of 8: 164 Node tests with 163 passed and 1 skipped,
the symlink case, and the engine container tests 190 of 190). Still not observed, so the P02 exit is not met in full:
`./start.sh` opening the window, a folder chosen in the native dialog, a `fast` run from the window, closing it stopping
the services, a GPU, and Chromium enforcing the renderer settings (the host has no Electron binary).

## 4. P03 — Local AI runtime

**Depends on:** P02. **Areas:** providers, models.

- [x] P03.1 Define the `VisionProvider` contract and implement the llama-server provider.
- [x] P03.2 Add `fetch-models` with pinned revisions and SHA-256 checks.
- [ ] P03.3 Run the [bake-off](ai-providers.md#bake-off) on the reference PC, and record the results.
- [ ] P03.4 Confirm or replace the default in [ADR 0004](decisions/0004-default-local-model.md).
- [x] P03.5 Remove the Ollama client, its settings, UI texts and tests.

Progress 2026-11-02 (P03.1): `engine/src/providers` holds the `VisionProvider` contract, the llama-server provider,
the verdict schemas with an in-repo validator, versioned prompts, copy preparation and a guard that degrades to the
prescore ([AI providers](ai-providers.md#provider-contract)). Observed: `bash scripts/check.sh --engine` passed, 224 of 224
engine tests (34 new, on a fake server and synthetic images). Not observed: a real llama-server, any model, and the
pipeline using the provider (P04).

Progress 2026-11-02 (P03.5): the earlier local runtime client, its settings, its Settings fields, its IPC channel,
its engine command and its legacy config block are removed, and one provider check (`GET /api/providers`, bridge method
`checkProviders`) replaces the old check in the bridge and in Settings. The engine's model stage now only probes the
`ai.local` provider; it sends no photo to a model until P04. See [Architecture](architecture.md#bridge-mapping) and
[assessment item B2](assessment.md). Observed: `npm run lint` and `npm run build` passed; `node --test` gave 165 passed
of 166 (1 skipped); `bash scripts/check.sh --engine` passed 225 of 225 engine tests. Not observed: a real llama-server,
any model, and the window.

Progress 2026-11-02 (P03.2): `./start.sh --fetch-models [model|all]` runs the one-shot `fetch-models` service
(`docker/fetch-models/Dockerfile`, `engine/src/model_fetch/`), on `egress` only, behind the profile `fetch`, with the
writable `models` volume as its only mount. The manifest pins the default model and the three bake-off candidates to
the commits, sizes and SHA-256 values the owner supplied; a file is renamed into place only after all three and its
GGUF or safetensors header match, and pickle-based formats are refused
([Operations](operations.md#model-files), [ADR 0011](decisions/0011-pinned-model-download.md)). Observed:
`npm run lint` passed; `npm run build:electron && node --test "tests/**/*.test.cjs"` gave 180 passed of 181 (1 skipped,
the symlink case); `bash scripts/check.sh --engine` passed 258 of 258 (33 new, local fake files);
`bash scripts/check.sh --compose` passed; `bash scripts/check.sh --integration` passed with every assertion, including
`fetch-models` reaching a public endpoint but not `api` or `gateway`, seeing no photos, and writing a models volume
that `vision` created first. Not observed: a real download from Hugging Face, so no pin has been checked against the
real files yet.

Exit: the default model completes the benchmark on the reference PC with at least 0.5 GiB of GPU
memory free at peak; no Ollama reference remains in the code; provider contract tests pass.

## 5. P04 — Curation pipeline

**Depends on:** P03. **Areas:** engine, database, reports.

- [x] P04.1 New ingest: content-hash IDs, working copies, one decode, orientation applied once.
- [x] P04.2 Sanity gate that rejects only objective failures.
- [x] P04.3 Q-ReAlign scoring and SigLIP 2 embeddings, batched on the GPU.
- [x] P04.4 Order-independent grouping, content tags and people signals with no identity.
- [x] P04.5 Shortlist, Swiss tournament in both orders, Bradley-Terry rank, verdict cache.
- [x] P04.6 Critique, diversity-aware selection, tiers, reports and exports.
- [x] P04.7 Persist per-stage progress so resume really works.
- [ ] P04.8 Remove the old gate, ranker, slots, OpenCLIP and LAION paths.

Progress 2026-11-02 (P04.1): `engine/src/ingest.py` hashes each original (read-only), decodes it once, applies the EXIF
orientation once, and writes an upright working copy (long side at most 1,536 px, never upscaled) and a 512 px
thumbnail; quality, faces and embeddings read only the working copy ([Pipeline](pipeline.md#1-ingest)). The photo ID is
the SHA-256 of the content, so a moved or renamed file keeps its results and an unchanged file is not decoded again;
byte-identical copies share one ID and only the first in path order is kept. Only capture time, camera make and model
are kept from EXIF. This closes assessment items B5, C1 and C3. Observed: `bash scripts/check.sh --engine` passed 282 of
282 engine tests (24 new, on synthetic images, counting decodes by wrapping `Image.open` and `cv2.imread`);
`bash scripts/check.sh --node` passed (lint, builds, 181 of 182 node tests, 1 skipped). Not observed: a full `analyze`
run with the new ingest (the resume path in `engine/main.py` is covered only through `index_photos`), and
`bash scripts/check.sh --integration`, whose photo-ID assertion changed to the new ID shape.

Progress 2026-11-02 (P04.3): `engine/src/neural_stages.py` runs Q-ReAlign (quality and aesthetics, stored 0 to 1 in
the `neural_scores` table) and SigLIP 2 (unit vectors in `embeddings`) in batches on the GPU, through
loaders that read local `.safetensors` only and never download ([Pipeline](pipeline.md#3-scoring)). A missing model gives
a logged `model_<reason>` error and unknown values. This addresses assessment items B8 (for the new path) and C2. The
heavy packages moved to `engine/requirements-gpu.txt`. Not observed: any test run (the container pytest command was
denied in this session), the real adapters, real weights, the pins in `requirements-gpu.txt`, and a full `analyze`
run; real-model verification is P04.MEASURE.

Progress 2026-11-02 (P04.4c): `engine/src/face_quality.py` now runs the MediaPipe Face Landmarker tasks API and
derives a small `people_penalty` (0 without a face) from the blink blendshapes and the local sharpness of the main
face box; no face vector is computed or stored ([Pipeline](pipeline.md#6-people-signals),
[ADR 0012](decisions/0012-people-signals-face-landmarker.md)). The `face-landmarker` entry of the fetch manifest pins
`face_landmarker.task` by object generation id and SHA-256, and nothing downloads it unless asked. This closes
assessment item D7. Observed: `bash scripts/check.sh --engine` passed 386 of 386 engine tests (333 before this change;
the new tests use a fake landmarker and fake files); `npm run lint` printed no findings. Not observed: the Angular and Electron builds and
the node tests (no node file changed), the real model, the real tasks API, and a real download of the file.

Progress 2026-11-02 (P04.5): `engine/src/shortlist.py`, `engine/src/tournament.py` and `engine/src/bradley_terry.py` add
the shortlist (top K group representatives, a cap per content tag), the Swiss tournament (`ceil(log2(K)) + 1` rounds,
every pair in both orders, a disagreement is a tie), the Bradley-Terry rank with a standard error and the prescore as a
weak prior, and the `verdicts` table that caches verdicts by content hash, provider, model and prompt version
([Pipeline](pipeline.md#7-shortlist), [ADR 0013](decisions/0013-tournament-rank-and-verdict-cache.md)). With no provider
the stage is skipped and the prescore decides. This closes assessment item B6 for the new path. Observed:
`bash scripts/check.sh --engine` passed 424 of 424 engine tests (386 before; 38 new, with a fake provider). Not
observed: a real model judging a pair, a full `analyze` run (the stage is not wired into `engine/main.py` until the
prescore exists in P04.6), the settings on a real collection (P04.MEASURE), the Angular and Electron builds and the
node tests (no node file changed).

Progress 2026-11-02 (P04.6): `engine/src/critique.py`, `engine/src/selection.py`, `engine/src/curation.py`,
`engine/src/curation_report.py` and `engine/src/curation_export.py` add the finalist critique (finalists only, run after
the rank and the selection are fixed, text only), maximal marginal relevance with `lambda = 0.75` giving nested tiers of
10, 25, 50 and 100 and a configurable final set (default 25), the prescore, `run_curation` (shortlist, tournament,
selection, critique), CSV and HTML reports that escape all model text, and exports of copies named `001_<original name>`
([Pipeline](pipeline.md#9-critique), [ADR 0014](decisions/0014-selection-critique-reports-and-exports.md)). This
addresses assessment items A3, A4, B3, B4, B7 and B9 for the new path; the legacy code goes in P04.8. Observed:
`bash scripts/check.sh --engine` passed 455 of 455 engine tests (424 before; 31 new, with a fake provider, synthetic
vectors and small files); `npm run lint` printed no findings. Not observed: a real model writing a critique, a full
`analyze` run (the stages are not called from `engine/main.py`, the API or the review screen; that is P04.7 and P04.8),
the selection settings on a real collection (P04.MEASURE), the Angular and Electron builds and the node tests (no node
file changed).

Progress 2026-11-02 (P04.7): `cmd_analyze` in `engine/main.py` now stores each photo's state per stage in the `jobs`
table as it goes: per photo for ingest, quality, faces and the gate, per GPU batch for Q-ReAlign and SigLIP 2, and once
for content tags. Each stage gets only the photos it has not finished, grouping is reused while a hash of its inputs is
unchanged (`stage_runs`), pause and cancel are read between photos and batches, and stored photos load back with their
exact values ([Pipeline](pipeline.md#resume), [ADR 0015](decisions/0015-per-stage-resume.md)). This addresses
assessment item B1. Wiring the curation stages of P04.5 and P04.6 into `analyze` moved to P04.8, so the output the
review screen reads changes once. Observed: `bash scripts/check.sh --engine` passed 473 of 473 engine tests,
including the new `engine/tests/test_resume.py`. Not observed: a kill of the process by a signal (the kills are
simulated by an exception), a real model, the Angular and Electron builds and the node tests (no node file changed).

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
