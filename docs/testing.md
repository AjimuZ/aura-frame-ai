# Testing

## Rules

- Automated tests use synthetic images only. Personal photos are never a test corpus.
- A check counts as passed only after it ran. Each result below carries a date.
- Tests live at public interfaces, and expected values come from outside the code under test.

## Single entry point

`bash scripts/check.sh` runs every check below that exists and exits 0 only when all of them pass.
A failing check is named in the summary and gives exit 1. A check whose files do not exist prints a
`SKIP:` line.

| Option | Runs |
| --- | --- |
| `--node` | Lint, Angular build, Electron build, `bash -n start.sh`, `node --test "tests/**/*.test.cjs"` |
| `--engine` | Engine tests in the throwaway container below. Fails if Docker is missing |
| `--compose` | `docker compose config --quiet`, with and without the `local` profile and the GPU overlay and with the `fetch` profile, then the static policy in `scripts/compose-policy.cjs` on every profile. Starts nothing |
| `--integration` | `--compose`, then starts the stack on a synthetic folder and tests its isolation from inside. See [below](#container-isolation) |
| `--backend-run` | `./start.sh --backend-only` on a synthetic folder, a `fast` run through the API, then `./start.sh --stop`. See [below](#backend-run) |
| none | `--node`, `--engine` and `--compose`; the integration check and the backend run are left out |

Status: written in P01.1; its first full run is not yet recorded here.

## Current checks

Results from 2026-11-01 on the reference PC.

| Check | Command | Result |
| --- | --- | --- |
| Lint | `npm run lint` | Pass, exit 0 |
| Electron build | `npm run build:electron` | Pass, exit 0 |
| Angular build | `npm run build:angular` | Pass, exit 0 |
| Library store tests | `node --test "tests/*-store.test.cjs"` | Pass, 4 of 4 |
| Electron hardening guard | `node --test tests/electron-hardening.test.cjs` | Pass, 3 of 3. Reads source and lockfile only. It does not start the app |
| Renderer isolation | `node --test tests/renderer-isolation.test.cjs` | Pass, 8 of 8 (2026-11-02, P02.EXIT). Loads the compiled `main.js` and `preload.js` against a stand-in for `electron`, with no new dependency. Covers: the window's `webPreferences` are exactly the preload, `contextIsolation`, `sandbox` and no `nodeIntegration`; the preload, run in an empty V8 context with a `require` that serves only `electron`, exposes one key, `auraFrame`, made only of functions, each making one `invoke` on a channel main handles; progress events reach the page without the IPC event; the CSP of the source and built `index.html` names no network source; new windows are denied; navigation is refused to any URL other than the built `index.html` (another local file, a UNC host, `http`, `data:`), and in development to anything but the dev server's origin (a look-alike host is refused). Gap: Chromium enforcing these settings in a real renderer is not observed, because the host has no Electron binary (`npm ci` was not run on it) |
| Model fetch | `engine/tests/test_model_fetch.py`, in the engine run below | Pass, 33 of 33 (2026-11-02, P03.2), with local fake files and no network. Covers: the manifest equals the pins the owner supplied (repository, commit, file, SHA-256, size, for the default and the three bake-off candidates); every entry has a 40-hex commit, a 64-hex SHA-256 and the GGUF format; a hash mismatch, a truncated or oversized download and a failing source raise and leave no file at all; a matching file is renamed into place and verified again without a download; a wrong existing file is replaced or removed; pickle-based names (`.bin`, `.pt`, `.pth`, `.pkl`, `.ckpt`, `.npy`) are refused before any download and in a manifest; a pickle named `.gguf` is refused by content; unpinned or unsafe manifest entries are refused; only https is opened and redirects stay on https; stale partial files are removed; the exit codes for an unknown model, a missing volume and a full disk. Gap: no real download from Hugging Face. Added in P04.4c: the `face-landmarker` entry equals the owner's pin (generation id, SHA-256, size), a task bundle is accepted by its ZIP header from its pinned URL, a pickle named `.task` is refused by content, and a URL pin is refused when it is http, on another host, without or with a different `generation`, or with extra query parameters. Gap: no real download of the Face Landmarker |
| People signals | `engine/tests/test_face_quality.py`, in the engine run below | Pass (`bash scripts/check.sh --engine`, 386 of 386 engine tests, 2026-11-02, P04.4c), with a fake landmarker and synthetic images. Covers: no face gives a penalty of exactly 0; open eyes on a sharp face give 0; closed eyes, a blurred main face and both give the weighted, capped penalty; a group counts the worst eyes and uses the largest face; unknown blinks add nothing; reduction of a synthetic mesh to scalars; no landmark value or face vector reaches the photo record or any database table; a missing model gives one `model_file_missing` event naming the fetch command, no penalty and no network call; a missing `mediapipe` gives `model_dependency_missing`; the model name must be one plain `.task` file with a ZIP header. Gap: the real model and the real `mediapipe` tasks API were not run |
| Shortlist and tournament | `engine/tests/test_tournament.py`, in the engine run below | Pass (`bash scripts/check.sh --engine`, 424 of 424 engine tests, 2026-11-02, P04.5), with a fake provider and no image. Covers: the top K group representatives with a per-tag cap, independent of input order; Swiss rounds equal `ceil(log2(K)) + 1` (K of 8 and 32, with 2 calls per pair and no rematch); an odd count and two photos; a disagreement between the orders is a tie; a rerun makes no provider call and loads no image; a changed model, prompt version or provider misses the cache, and the cache knows the pair order; failed calls are not cached; Bradley-Terry gives ranks and uncertainties, stays finite for unbeaten photos, is invariant to the prescore scale, and the prescore is a weak prior; no provider, a provider not ready and a dead or degraded provider skip the stage and leave the prescore order. Gap: no real model judged a pair, and the stage is not wired into `analyze` |
| Critique, selection, reports and exports | `engine/tests/test_curation.py`, in the engine run below | Pass (`bash scripts/check.sh --engine`, 455 of 455 engine tests, 2026-11-02, P04.6), with a fake provider, synthetic vectors and small files under a temporary folder. Covers: the critique is requested for the finalists only, and the ranking and the selection are identical with no critique, with different critiques and with empty ones; critique text is bounded and cleaned, and a failing, raising or not-ready provider and an unreadable copy cost only their finalist; MMR with `lambda = 0.75` gives nested tiers of 10, 25, 50 and 100 and a configurable final set (default 25), clamped for a small pool and limited to the pool size; a look-alike is pushed behind a different photo, lambda 1 is the rank alone, and a missing, zero or differently sized vector is never compared; running twice, with ties, with shuffled input and without a provider gives the same result and no new comparison call; the prescore formula, the stored library and the working-copy loader; exports are copies named `001_<original name>` that widen past 999, keep equal names apart, leave originals byte-identical with the same modification time, refuse a link or a folder holding an original, and replace only earlier numbered files; the HTML is parsed to prove that a `<script>`, an `<img onerror>`, a `<b>`, an `onclick` attribute and quotes in model text and in a file name appear only as escaped text, and the CSV neutralizes `=`, `+`, `@` cells and keeps a line break inside one row. Gap: no real model wrote a critique, a browser and a spreadsheet did not open the reports, and the stages are not called from `analyze` |
| Engine tests, container | See below | Pass, 258 of 258 (re-run 2026-11-02 after P03.2; 224 after P03.1), without the torch stack. `engine/tests/test_providers.py` (34) runs the llama-server provider against a fake server on 127.0.0.1 and synthetic images: valid and invalid output, bounded retries, a timeout, a missing model, copy preparation (JPEG, quality 90, 1,024 px, no metadata, checked by re-reading the bytes), clamped limits and the fallback to the prescore. No model was run. The export and run tests use synthetic images and a stub engine, and prove that copies go under the exports root and never under `/data`, that a symlinked folder or file is not followed, that nothing is copied unless every ID passes, and that a folder the API user cannot write gives a structured 409. As the container runs as root, the unwritable case is simulated (a file in place of the folder, and `os.access` returning false); a real uid 10001 against a host folder was not observed |
| Compose configuration and policy | `bash scripts/check.sh --compose` | Pass, exit 0 (re-run 2026-11-02, P03.2, with the `fetch` profile; the policy requires `fetch-models` on `egress` only, behind a profile, with the writable `models` volume as its only mount, and refuses any other service on a routed network) |
| Container isolation | `bash scripts/check.sh --integration` | Pass, all assertions hold, with the `core` stage of `api`, no GPU and no model (re-run 2026-11-02, P03.2: network, read-only, port, socket, token and Host assertions, plus the `fetch-models` ones below) |
| Backend run | `bash scripts/check.sh --backend-run` | Pass on 2026-11-02 (P02.EXIT): `./start.sh --backend-only --cpu --provider none` with the `full` image, 6 synthetic images, a `fast` run that ended with step `done` (6 of 6) in about 3 s, a `done` event and no `error` event in the stream, the 6 photos listed by ID, and the folder unchanged. Gap: no window, no GPU and no local model |
| `start.sh` contract | `node --test tests/start-sh.test.cjs` | Pass, 30 of 30 (2026-11-02, P03.2), with stubbed `docker`, `nvidia-smi`, `curl`, `uname` and window. Covers `--fetch-models` (one `compose --profile fetch run --rm --build fetch-models [model|all]`, no `up`, GPU, token or window, exit 1 on a failed download, exit 2 on a malformed name), no other flow naming the `fetch` profile, the manifest's default files matching the `vision` defaults, exit codes 2, 1 and 3, the token never being an argument, the model guidance, `MSYS_NO_PATHCONV=1`, the memory warning, the window (run after the services, the token only in its environment, the host fallback, one `down` that keeps the volumes after a close, a failure, SIGINT or SIGTERM), and the desktop shell build (digest pin required, the recorded pins chosen by host platform, build inputs only, read-only). The stubbed tests cannot reach a real daemon, so the real run is the next row |
| Desktop shell build, real | `./start.sh --rebuild --cpu` | Pass on 2026-11-02 on the reference PC: exit 0, and the `:wine` image wrote the NSIS installer and `win-unpacked/` into `release/`, then the services became healthy ([Operations](operations.md#desktop-shell-build)). Gap: the built app not run, the GPU variant cut by the session's 10-minute limit before its exit code, no Linux host |
| Electron compose stack | `node --test tests/compose-stack.test.cjs` | Pass, 18 of 19 and 1 skipped (the symlink case, when the OS refuses symlinks). Uses a fake `spawn`: checks the argv, `shell: false`, the photo folder, the exports folder (`AURAFRAME_EXPORTS_DIR`) and the token only in `env`, the exports folder absent from the argv and refused when relative, a file, inside the photo folder or holding it, `-f compose.gpu.yaml` added when `nvidia-smi -L` succeeds and left out when it fails or is missing (probe run with `shell: false`), `down` naming the same files as `up`, the token absent from replies and events, when `isUp` holds, and `down` waiting for an `up` in flight. Gap: not run against a real Docker daemon or a real `nvidia-smi` |
| Electron analysis over HTTP | `node --test tests/analysis-http.test.cjs` | Pass, 30 of 30 (re-run 2026-11-02 after P02.4.5), with a fake HTTP server, a stand-in for `electron` and a fake `spawn`. Covers the token and Host header on every route, no request before a successful start or after a stop, the token absent from every reply and event (also when the server echoes it), the folder absent from API requests, stored paths absent from the photo list, SSE parsing, reconnect without repeated events, cancel and a refused cancel, `down` only after `up` when the app closes during a start, the fallbacks when the API is down, and the channel list against the preload. Gap: not run against the real API, gateway, Docker or Electron |
| Electron media proxy | `node --test tests/media-proxy.test.cjs` | Pass, 10 of 10, with a fake HTTP server. Covers no request and no token read before a start or after a stop, the thumbnail route with the token and Host header, fixed response headers, malformed requests, refusals, redirects, wrong types, oversize, truncated and timed-out answers, a refused connection, the limit of 8 requests at once, and, from source only, the pipe and the absence of stored paths in the renderer. Gap: not run against the real API or gateway, or in a running Electron |
| Electron single-photo export and library deletion | `node --test tests/bridge-handlers.test.cjs` | Pass, 15 of 15 (re-run 2026-11-02 after P02.4.5), with a fake HTTP server, a stand-in for `electron` and a temporary folder of synthetic bytes. Covers `fs:openOutputFolder` and `fs:openReportsFolder` opening `<exports>/<library id>/output` and `/reports`, the same host folder a started `ComposeStack` mounts at `/exports`, the exports folder opening before anything is written without creating a library folder, the host copies of a deleted library being removed, the copy through the mapped host path, the refusal of stored paths outside `/photos` or the folder (dot segments, backslash, NUL, a directory junction), a destination equal to the original, fixed messages with no path or token, `DELETE .../data` with the token before the registry removal, and nothing sent while the services are not up. The refused start that clears "Starting..." is in `tests/analysis-http.test.cjs`. Gap: no real API, and the native save dialog is a stub |
| Electron quit paths | `node --test tests/quit-handling.test.cjs` | Pass, 6 of 6, with a fake `app`, plus the compiled `main.js` loaded against a stand-in for `electron` and a fake server. Covers `before-quit` without `window-all-closed`, no quit loop, macOS keeping the app, the 10-minute bound (run with a short one), and no request from the real wiring before a start, `afmedia` included. Gap: `File > Exit`, the dialog and the exit have not been observed in a running Electron |
| Design system color check | See [Design system](design-system.md#verification) | **Fails:** 13 literals outside the token file, fixed in P06 |
| Electron 33 to 45 breaking changes | Not checked | Gap: sessions have no web access, so upstream release notes could not be read. P01.SMOKE is what observes the app running |
| Angular unit tests | None exist | Gap |
| End-to-end tests | None exist | Gap |

`build:electron` must run before `node --test "tests/*-store.test.cjs"`. `npm run test:libraries`
does both.

### Engine tests in a throwaway container

Python runs only in containers, so this is the only way to run the engine tests. It leaves the host
untouched, and mounts only `engine/`, read-only.

```bash
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$(pwd)/engine:/app/engine:ro" \
  python:3.12-slim bash -c '
    apt-get update -qq && apt-get install -y -qq libgl1 libglib2.0-0
    pip install -q -r /app/engine/requirements.txt
    cd /app && python -m pytest engine/tests -q -p no:cacheprovider'
```

In Git Bash on Windows, prefix the command with `MSYS_NO_PATHCONV=1` and use `$(pwd -W)`.

The check installs `engine/requirements.txt` only. The heavy inference stack (torch, transformers, safetensors)
is in `engine/requirements-gpu.txt`, which only the `full` stage of the `api` image installs, and a test fails if a
heavy package returns to `requirements.txt`. An older form of this command filtered `torch`, `open-clip-torch` and
`timm` out with `grep -vE`; it still works, because the filter now removes nothing.

Limit: the suite has no torch stack and no model files. The Q-ReAlign and SigLIP 2 stages are tested with fake
models (batching, device request, 0 to 1 values, unit-length vectors, unknown values, safetensors-only loading, no
download calls). The real adapters in `engine/src/gpu_models.py`, the real checkpoints and the pins in
`requirements-gpu.txt` are unverified until P04.MEASURE and the first build of the `full` image. The legacy
OpenCLIP and LAION paths are not covered.

Where exports and reports go in the tests: each test builds its own `data`, `photos` and `exports` roots under
pytest's temporary folder. The API writes copies to `<exports>/<library id>/output/top_06_final_set` and the
engine writes reports to `<exports>/<library id>/reports`, the same layout as `/exports/<library id>/` in the
container and as `<exports>/<library id>/` on the host. The Node tests use a temporary `exports` folder next to
the temporary library registry and never the real `userData` folder.

### Container isolation

`tests/integration/compose-stack.sh` starts the real services under the project name
`auraframe-integration`, with its own volumes, and removes them on exit. It uses a synthetic folder and no
model file. Each negative result has a control, so a broken probe cannot pass as a blocked connection.

| Property | How it is shown | Control |
| --- | --- | --- |
| `api` and `vision` have no internet | TCP connections to two public addresses and one public name fail, from inside each service | The same probe reaches other services on `backend`, and from the same image on Docker's default network it reaches a public endpoint. Without that, the check fails as unproven |
| `/photos` cannot be written | `touch`, `mkdir`, append and `rm` fail with `Read-only file system`, as the service user and as root | The folder on the host is world-writable, `/proc/self/mounts` shows a separate `ro` mount, the synthetic file is readable and stays unchanged, and `api` can write to `/data` |
| Only `gateway` publishes a port | `docker inspect` shows `127.0.0.1:<port>` for `gateway`, nothing for `api` and `vision` | Through that port, HTTP 200 with the token, 401 without it, 403 for a foreign Host |
| Non-root, no Docker socket, read-only root | `id -u`, `test ! -e /var/run/docker.sock`, and a refused write to `/etc` in each service | `llama-server --version` runs as the service user |
| `fetch-models` is isolated apart from its download route | `up` does not start it. In throwaway `compose run` containers it runs as uid 10001, has no `/photos`, `/data`, `/exports` or Docker socket, cannot write `/etc`, and cannot connect to `api:8000` or `gateway:8080` | It reaches a public endpoint through `egress`, and the fetcher starts with `--list` (no download) on the read-only filesystem |
| `fetch-models` can write the models volume | A probe file is created and removed in `/models` as uid 10001 | The volume is created first by a `vision` listing, as `start.sh` does, before anything else mounts it |

Limits: `api` is built from the `core` stage, and `vision` runs `sleep` instead of `llama-server`, because
the check has no model and must not download one. `fetch-models` downloads nothing in this check. Neither the GPU
overlay nor the `full` stage is started. The check needs internet access for its control, and fails on an offline host.

### Backend run

`tests/integration/backend-run.sh` runs `./start.sh --backend-only --cpu --provider none` with a temporary photo
folder and a temporary exports folder, then draws synthetic images into the photo folder with a throwaway container
of the `api` image that has no network. It reads the token from `data/session-token`, starts a `fast` run for the
library `exit-check-synthetic` through the gateway, and polls `runs/current` for up to 8 minutes.

| Property | How it is shown |
| --- | --- |
| The path without the window works | `POST .../runs` answers 202, and `runs/current` ends with `"step":"done"` |
| The engine did not fail | The replayed event stream has a `done` event and no `error` event |
| Results are served by ID | `GET .../photos` lists one content-hash ID (64 hex digits) per synthetic image |
| Originals stay unchanged | Name, size and checksum of every file match before and after the run |

On exit it deletes the library's generated data through the API and runs `./start.sh --stop`, which keeps the
volumes. Unlike `--integration`, it uses the real project `auraframe` and its volumes, so it also stops services
that were running before it. The token file stays, as after any `--backend-only` start, and is stale once the
services stop. It downloads no model; the `full` image must already be built, or the first start builds it.

## Planned checks

| Package | Check | Exit condition |
| --- | --- | --- |
| P01 | Retired-terms search | No match in the repository. The test holds the term list in encoded form so the repository stays clean |
| P02 | Smoke test | `./start.sh` on a synthetic folder completes a `fast` run end to end, through the window |
| P03 | Provider contract tests | Fake server: valid, invalid, timeout, missing model |
| P03 | Bake-off report | Accuracy, order stability, time, peak memory, for each candidate on this PC |
| P04 | Regression corpus | See below |
| P04 | Resume test | Kill a run mid-stage. The rerun repeats no finished work |
| P04 | Idempotence test | Reranking twice gives the same result |
| P05 | Codex contract tests | Fake CLI: schema output, failure, call cap, no opt-in means no call |
| P05 | Data-shape test | What reaches the sidecar has no EXIF, no file name, no path |
| P06 | Design checks | See [Design system](design-system.md#verification) |
| P06 | Interface tests | Angular with the mock API, under `ng serve`, in Chromium |
| P06 | End-to-end test | Playwright's Electron API (experimental upstream) against the running stack, with synthetic photos |

## Regression corpus (P04)

Each case is generated in code and turns a defect from the [assessment](assessment.md) into a
permanent test.

| Synthetic image | Must happen |
| --- | --- |
| Smooth gradient, a proxy for fog or bokeh | Survives the gate |
| Black sky with bright points, a proxy for a night sky | Survives the gate |
| Image with EXIF orientation 6 | The model receives the upright version |
| Photo with no face | Reaches the shortlist if its scores earn it |
| Unchanged file on a second run | Skipped by hash |
| Moved file with the same bytes | Keeps its results |
| Byte-identical copy | Rejected as an exact duplicate |
| Corrupt file | Recorded as unreadable, and the run continues |
