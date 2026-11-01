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
| Engine tests, container | See below | Pass, 27 of 27, in 1.65 s, without the torch stack |
| Compose configuration and policy | `bash scripts/check.sh --compose` | Pass, exit 0 |
| Container isolation | `bash scripts/check.sh --integration` | Pass, all assertions hold, with the `core` stage of `api`, no GPU and no model |
| `start.sh` contract | `node --test tests/start-sh.test.cjs` | Pass, 11 of 11, with stubbed `docker`, `nvidia-smi`, `curl` and `uname`. Covers exit codes 2, 1 and 3, the token never being an argument, the model guidance, `MSYS_NO_PATHCONV=1` and the memory warning. Gap: not run against a real Docker daemon |
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
