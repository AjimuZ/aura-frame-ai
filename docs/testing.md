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
| `--node` | Lint, Angular build, Electron build, `node --test "tests/**/*.test.cjs"` |
| `--engine` | Engine tests in the throwaway container below. Fails if Docker is missing |
| `--integration` | Slow container integration checks. None exist yet, so it prints `SKIP` (P02 adds them) |
| none | `--node` and `--engine`; integration checks are left out |

Status: written in P01.1; its first full run is not yet recorded here.

## Current checks

Results from 2026-11-01 on the reference PC.

| Check | Command | Result |
| --- | --- | --- |
| Lint | `npm run lint` | Pass, exit 0 |
| Electron build | `npm run build:electron` | Pass, exit 0 |
| Angular build | `npm run build:angular` | Pass, exit 0 |
| Library store tests | `node --test "tests/*-store.test.cjs"` | Pass, 4 of 4 |
| Engine tests, npm script | `npm run engine:test` | **Fails, exit 6:** no Python 3.12 runtime on this PC |
| Engine tests, container | See below | Pass, 27 of 27, in 1.65 s, without the torch stack |
| Design system color check | See [Design system](design-system.md#verification) | **Fails:** 13 literals outside the token file, fixed in P06 |
| Angular unit tests | None exist | Gap |
| End-to-end tests | None exist | Gap |

`build:electron` must run before `node --test "tests/*-store.test.cjs"`. `npm run test:libraries`
does both.

### Engine tests in a throwaway container

This leaves `.venv` and the host untouched, and mounts only `engine/`, read-only.

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

## Planned checks

| Package | Check | Exit condition |
| --- | --- | --- |
| P01 | Retired-terms search | No match in the repository. The test holds the term list in encoded form so the repository stays clean |
| P02 | `docker compose config --quiet` | Valid configuration |
| P02 | Network isolation test | From `api` and `vision`, connections to the internet fail |
| P02 | Read-only test | From `api`, a write under `/photos` fails |
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
