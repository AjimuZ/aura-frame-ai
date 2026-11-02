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
| `--compose` | `docker compose config --quiet`, with and without the `local` profile and the GPU overlay, then the static policy in `scripts/compose-policy.cjs`. Starts nothing |
| `--integration` | `--compose`, then starts the stack on a synthetic folder and tests its isolation from inside. See [below](#container-isolation) |
| none | `--node`, `--engine` and `--compose`; the integration check is left out |

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
| Engine tests, container | See below | Pass, 190 of 190 (re-run 2026-11-02 after P02.4.5), without the torch stack. The export and run tests use synthetic images and a stub engine, and prove that copies go under the exports root and never under `/data`, that a symlinked folder or file is not followed, that nothing is copied unless every ID passes, and that a folder the API user cannot write gives a structured 409. As the container runs as root, the unwritable case is simulated (a file in place of the folder, and `os.access` returning false); a real uid 10001 against a host folder was not observed |
| Compose configuration and policy | `bash scripts/check.sh --compose` | Pass, exit 0 |
| Container isolation | `bash scripts/check.sh --integration` | Pass, all assertions hold, with the `core` stage of `api`, no GPU and no model |
| `start.sh` contract | `node --test tests/start-sh.test.cjs` | Pass, 26 of 26 (2026-11-02, P02.WINDOW), with stubbed `docker`, `nvidia-smi`, `curl`, `uname` and window. Covers exit codes 2, 1 and 3, the token never being an argument, the model guidance, `MSYS_NO_PATHCONV=1`, the memory warning, the window (run after the services, the token only in its environment, the host fallback, one `down` that keeps the volumes after a close, a failure, SIGINT or SIGTERM), and the desktop shell build (digest pin required, the recorded pins chosen by host platform, build inputs only, read-only). The stubbed tests cannot reach a real daemon, so the real run is the next row |
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
    grep -vE "^(torch|open-clip-torch|timm)==" /app/engine/requirements.txt > /tmp/req.txt
    pip install -q -r /tmp/req.txt
    cd /app && python -m pytest engine/tests -q -p no:cacheprovider'
```

In Git Bash on Windows, prefix the command with `MSYS_NO_PATHCONV=1` and use `$(pwd -W)`.

Limit: without the torch stack, embeddings and aesthetics run on their fallback paths. Those paths
are what the suite asserts. The OpenCLIP and LAION paths are not covered.

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

Limits: `api` is built from the `core` stage, and `vision` runs `sleep` instead of `llama-server`, because
the check has no model and must not download one. Neither the GPU overlay nor the `full` stage is started.
The check needs internet access for its control, and fails on an offline host.

## Planned checks

| Package | Check | Exit condition |
| --- | --- | --- |
| P01 | Retired-terms search | No match in the repository. The test holds the term list in encoded form so the repository stays clean |
| P02 | Token and Host test | Requests without the session token, or with a foreign `Host`, are refused |
| P02 | Electron hardening test | The renderer has no Node, no network and only `window.auraFrame`, and cannot navigate away |
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
