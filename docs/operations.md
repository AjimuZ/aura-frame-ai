# Operations

Status: **partly implemented** ([P02](roadmap.md)). `compose.yaml` and `start.sh` exist, and the desktop app's
analysis handlers call the stack over HTTP ([Bridge mapping](architecture.md#bridge-mapping)). Of the `start.sh`
contract below, the services, the checks, the token, the exit codes and the window are implemented. The window is
tested with stubs only and has not been opened by `start.sh` on a real host yet. `--fetch-models`, `--codex-login`
and `--provider codex` are planned, and the script says so (see [Not available yet](#not-available-yet)). To run
the current code, see [Running the current code](#running-the-current-code).

## Compose stack

```bash
AURAFRAME_API_TOKEN=<at least 16 characters> AURAFRAME_PHOTOS_DIR=<folder> \
  docker compose --profile local -f compose.yaml -f compose.gpu.yaml up -d --build
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `AURAFRAME_API_TOKEN` | none | Session token. `api` refuses to start without one |
| `AURAFRAME_PHOTOS_DIR` | `./data/no-photos` | Folder mounted read-only at `/photos` |
| `AURAFRAME_EXPORTS_DIR` | `./data/output` | Folder mounted at `/exports`. The API writes `/exports/<library id>/output` (copies) and `/exports/<library id>/reports` there. The app sets it to `exports/` in its `userData` folder |
| `AURAFRAME_PORT` | `8420` | Port of `gateway` on `127.0.0.1` |
| `AURAFRAME_API_TARGET` | `full` | Build stage of `api`. `core` leaves out PyTorch |
| `AURAFRAME_VISION_MODEL`, `AURAFRAME_VISION_MMPROJ` | The files in [AI providers](ai-providers.md#server-settings) | Model files in the `models` volume |

- Leave out `--profile local` to run without `vision`. Leave out `compose.gpu.yaml` on a host without a GPU.
- `vision` does not become healthy until its model files are in the `models` volume, which nothing fills
  yet (P03.2). Until then, run without the profile.
- On Linux, create the exports folder first, writable by uid 10001 (for example `chown 10001:10001` on it).
  Docker otherwise creates it as root. The API runs as uid 10001 and writes copies and reports there: when it
  cannot, an export or an analysis start answers HTTP 409 `export_folder_unwritable` and the window shows
  "The export folder cannot be written by the analysis service", instead of failing later or leaving the folder
  empty. Deleting a library whose folder cannot be cleaned says "The generated data could not be removed" and keeps
  the library. The app creates its own
  exports folder with your account, so on Linux change its owner (or mode) once to uid 10001; on Docker Desktop
  for Windows the folder is writable as it is. Neither was observed on a real host.
- Image builds do not download model files.

## Prerequisites

| Requirement | Windows | Linux |
| --- | --- | --- |
| Docker with Compose v2 | Docker Desktop with the WSL2 backend | Docker Engine |
| GPU, optional | NVIDIA driver on Windows | NVIDIA driver and `nvidia-container-toolkit` |
| Shell for `start.sh` | Git Bash | Any POSIX shell |
| Desktop session | Yes, the app opens a window | Yes |
| Disk | About 35 GB for images and models (estimate) | Same |

Node.js is not required on the host, because the desktop shell is built in a container. Without a
compatible GPU, the application runs on CPU with `ai.provider: none`.

## start.sh

```bash
./start.sh                                # start the services and open the window
./start.sh --fetch-models                 # download model files, then exit
./start.sh --provider local|codex|none    # choose the AI provider
./start.sh --codex-login                  # sign in to Codex with a ChatGPT account
./start.sh --cpu                          # run without a GPU
./start.sh --rebuild                      # rebuild the images and the desktop shell
./start.sh --backend-only                 # services only, for development
./start.sh --status                       # show service health
./start.sh --logs [service]               # follow logs
./start.sh --stop                         # stop everything, keep the data
```

Contract:

- Exit codes: `0` success, `1` failed prerequisite, `2` invalid arguments, `3` a service failed
  its health check.
- It checks Docker, Compose, the GPU, Docker memory, and the presence of the model files, then
  explains exactly what is missing.
- It builds the images and the desktop shell only when they are missing or their sources changed.
- It never downloads a model on its own. Without model files it offers the `--fetch-models`
  command, and it can start with `ai.provider: none`.
- It generates a new API token per session, and passes it through the environment, never as an
  argument.
- It runs the window in the foreground. When the window closes, or on Ctrl+C, it stops the
  services, which frees the GPU. All data stays.
- The API port binds only to `127.0.0.1`. The default is `8420`, and users do not need it.
- It holds no secrets. It reads configuration from the environment and `config.yaml`.
- On Windows it sets `MSYS_NO_PATHCONV=1`, so that Git Bash does not rewrite container paths.

Behaviour as implemented:

- Arguments are checked first, so exit `2` never depends on the host. Two action flags together
  (`--status`, `--logs`, `--stop`, `--fetch-models`, `--codex-login`) are invalid.
- Prerequisites (exit `1`): the Docker CLI, a reachable daemon, Compose v2, and `nvidia-smi` unless
  `--cpu`. Docker memory under 10 GB only warns. A failed image build also exits `1`.
- Provider: without `--provider`, `local` is used when both model files are in the `models` volume, and
  `none` otherwise, with an explanation. `--provider local` with missing files exits `1`. The files are
  listed read-only through a throwaway `vision` container, which downloads no model.
- Start: `docker compose up -d --build --wait`, with `--profile local` only when the local provider runs
  and `compose.gpu.yaml` only without `--cpu`. `--rebuild` first builds the images without the cache, then the
  [desktop shell](#desktop-shell-build), which `--backend-only` leaves out. Then the script asks the gateway for
  `/health` with the token. A failed `up` or `/health` gives exit `3`.
- The token is 64 hex characters from `/dev/urandom`. Compose reads it from the environment, `curl` reads it
  from a configuration on stdin, and it is never printed. `--backend-only` also writes it to
  `data/session-token`, readable by the user only and ignored by Git.
- Window: without `--backend-only`, the script finds the window before it starts any service. It takes the shell
  built into `release/` (`release/win-unpacked/AuraFrame AI.exe` on Windows, `release/linux-unpacked/aura-frame-ai`
  on Linux, the second not observed), and otherwise the host fallback: `node_modules/electron/dist/electron .` when
  `npm ci` and `npm run build` have been run. With neither, it builds the shell once, as `--rebuild` does, and exits
  `1` if there is still no window. The services are not started in that case.
- After `/health` answers, the window runs in the foreground until it closes. It inherits `AURAFRAME_API_TOKEN`,
  `AURAFRAME_PORT` and `AURAFRAME_PROJECT_DIR` (the folder that holds `compose.yaml`) through the environment, never
  through an argument, and no token file is written. Electron main removes the token from its environment at launch.
  Because the token is the one the services already accept, the window shows the results of earlier runs at once,
  and a run started from it sends the same token. A window started separately has no token to inherit: it generates
  its own, sends nothing until its first run, and that run makes compose recreate `api` with the new token.
- When the window closes, the script runs `docker compose --profile local down` once and keeps the volumes. Ctrl+C
  (SIGINT), SIGTERM and SIGHUP end the window first, then do the same. The exit code is `0` after a normal close,
  `1` when the window exited with another code or `down` failed (the script then says to run `--stop`), and `130`,
  `143` or `129` after a signal. On Windows the window is a native program, so the `kill` from Git Bash is expected
  to end it without its own quit handling; the script's `down` then stops the services. If the window had a
  `docker compose up` of its own in flight at that moment, it may finish after that `down`: run `./start.sh --stop`.
- Tested with stubs only (`tests/start-sh.test.cjs`). Not observed: the real window opened by the script, Ctrl+C in a
  real terminal, and the Linux shell path.
- `--status`, `--logs [service]` and `--stop` only run the matching `docker compose` command.
  `--stop` keeps the volumes.

### Not available yet

These flags are valid, but their feature comes later. Each prints a "not available yet" message and exits
`1` without calling Docker or downloading anything:

| Flag | Planned in |
| --- | --- |
| `--fetch-models` | P03.2 |
| `--codex-login`, `--provider codex` | P05.2 |

## First start

```bash
./start.sh --fetch-models                 # about 9.3 GB for the default local model
./start.sh                                # then choose a folder in the window
```

The folder dialog is native. Electron mounts the chosen folder read-only in the `api` service.

## Desktop shell build

Status: **works on the reference PC (Windows) as of 2026-11-02** (P02.5). Not yet run on a Linux host.

`./start.sh --rebuild` builds the Electron app into `release/` (Git-ignored) with `electron-builder`, in the official
`electronuserland/builder:wine` image on Windows and `electronuserland/builder` on Linux, pinned by digest.
This route rules out native runtime dependencies without prebuilds. The app's runtime dependencies have
none. The native modules in build tools (`lmdb`, `@parcel/watcher`) run only inside the container.

- The digests are the constants `BUILDER_IMAGE_WINDOWS` and `BUILDER_IMAGE_LINUX` in `start.sh`. They were read with
  `docker inspect --format '{{index .RepoDigests 0}}'` after pulling the tags on 2026-11-02:

  | Tag | Pinned as |
  | --- | --- |
  | `electronuserland/builder:wine` | `electronuserland/builder@sha256:f3a8d451994c881d0972e7b1846ee4d4fdc15eb41fc6b1f350b38cbf99ca90fe` |
  | `electronuserland/builder:latest` | `electronuserland/builder@sha256:22c93521ff6ea682e7ef153bb4846d90b769aa3b3f3f3be9ec9090aabc3e877c` |

  To move a pin, pull the tag, read its digest the same way and edit the constant. If a constant is empty,
  `--rebuild` prints a warning and builds no shell.
- `AURAFRAME_BUILDER_IMAGE` overrides the constant for the host's platform. A value that is not
  `name@sha256:<64 hex characters>` exits `1`, so a floating tag is never run.
- The container gets only `package.json`, `package-lock.json`, `angular.json`, the three `tsconfig` files and `apps/`,
  all read-only, and `release/` to write to. It never sees the repository root, so the personal folders, `data/` and
  the session token stay out. It runs as the host's user with all capabilities dropped. It needs the internet for npm
  and the Electron download.
- A failed build exits `1` before the services start. A start without `--rebuild` builds the shell only when neither
  it nor the host fallback is there (see [start.sh](#startsh)).

Observed on the reference PC (Windows 11, Docker Desktop with the WSL2 backend and 7 GB of memory for Docker) on
2026-11-02:

- `./start.sh --rebuild --cpu` exited `0`. In the `:wine` image it ran `npm ci`, the Angular and Electron builds and
  `electron-builder --win`, and wrote `release/AuraFrame AI Setup 1.0.0.exe` (NSIS, x64), its `.blockmap` and
  `release/win-unpacked/` (with `resources/app.asar`). Then the services were rebuilt and started, and the gateway
  answered `/health`.
- An earlier `./start.sh --rebuild` (with the GPU overlay) produced the same files, but the session's 10-minute
  command limit stopped the script during its later steps, so its exit code was not observed. The `--no-cache`
  build of the GPU image takes most of that time.
- The build log shows `npm ci` reporting 80 audit findings in the locked dependency tree (2 low, 51 moderate, 24
  high, 3 critical) and a warning that six packages run install scripts. The installer is unsigned and uses
  the default Electron icon, and the log names no Wine step.
- Not observed: the installer or `win-unpacked/AuraFrame AI.exe` being run, and the Linux route.

The host fallback is therefore not needed on this PC. If the route fails on another host, the fallback is to build on
the host with Node.js:

```bash
npm ci && npm run build && npx electron .
```

## Development

```bash
./start.sh --backend-only     # services, and the session token in an ignored local file
npm start                     # Angular dev server and Electron, reading that token
npm run start:angular         # Angular alone, in a browser, with the mock API
```

`npm start` sets `AURA_DEV=1`, and only then does Electron main read `data/session-token`. A token in the environment
comes first. Either must be exactly 64 lowercase hex characters, or it is ignored and main generates its own. With a
token, main sends requests from launch, so the results of earlier runs show. If the services have been stopped or
restarted since, the gateway answers 401 or 403: main then sends nothing more, and the window says "The analysis
services are unavailable, because they refused this session. Start an analysis to restart them." A file left from an
earlier `--backend-only` start is harmless in the same way, because each start makes a new token.

## Memory on Windows

Docker Desktop limits containers to half of the system memory by default. On the reference PC
that means **8.2 GB of 15.8 GB**, which is too tight for model loading alongside the engine.

Recommended `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=12GB
swap=16GB
```

Apply it with `wsl --shutdown` and a Docker Desktop restart. This has **not been applied** on the
reference PC, because it affects every WSL distribution and every container on the computer.
`start.sh` warns when Docker has less than 10 GB.

Electron itself runs outside this limit, on the remaining system memory.

## Where data lives

| Data | Location | Removed by |
| --- | --- | --- |
| Originals | The folder you chose, read-only | Never |
| Library list, settings, encrypted key | Electron `userData` | Uninstalling or deleting the folder |
| Exports, reports | `exports/<library id>/` in the app's `userData` folder (the output and reports buttons open it); `./data/output` for `start.sh` and a bare compose | The app, per library, when the library is deleted; or delete the folder |
| Database, thumbnails, working copies | Volume `auraframe_data` | The app, per library, or `docker volume rm` |
| Model files | Volume `auraframe_models` | `docker volume rm` |
| Codex credentials | Volume `auraframe_codex_home` | `docker volume rm` |

## Troubleshooting

| Symptom | Cause | Action |
| --- | --- | --- |
| Window says the services are unavailable | The services were stopped or restarted with another token, or a service failed | Start an analysis from the window, or run `./start.sh --status`, then `--logs` |
| Desktop shell build fails | Container route not working on this host | Use the [host fallback](#desktop-shell-build) |
| GPU not found in a container | Driver or runtime | Run `docker run --rm --gpus all <image> nvidia-smi`, and update the driver |
| `vision` out of GPU memory | Other programs use VRAM | Close GPU-heavy apps, or set a smaller model build |
| `vision` or `api` killed | Docker memory limit | Apply the [memory setting](#memory-on-windows) |
| Slow analysis of a large folder | Windows bind mount | Working copies help after the first pass. Moving the photos into the WSL filesystem helps further |
| Port in use | Another service on 8420 | Set `AURAFRAME_PORT` |
| Model missing | Weights not downloaded | Run `./start.sh --fetch-models` |
| Codex rejected | Not signed in, or plan limit | Run `./start.sh --codex-login` and check the account limits |
| Provider down during a run | Crash or timeout | The run continues on the prescore. Rerun to complete the missing verdicts |

## Running the current code

Python runs only in containers. No host interpreter and no virtual environment are used or needed.

```powershell
npm install
npm start
```

Starting an analysis from the window runs `docker compose up -d --wait` with the chosen folder and the exports
folder, adding `-f compose.gpu.yaml` when `nvidia-smi -L` succeeds, then calls the
API on `127.0.0.1` (`AURAFRAME_PORT`, default `8420`). Docker must be running, otherwise the app reports that the
services could not be started. Started this way, the window has no token to inherit: until a run starts the
services, it sends the API nothing and shows no photos, including those of earlier runs; saving a review, exporting
and clearing data report that the service is not reachable. Opened by `./start.sh`, or by `npm start` after
`./start.sh --backend-only`, it shows earlier results at once (see [Development](#development)). This path is tested
with a fake server and a fake Docker only, and has not been run against the real stack. Run the engine tests with the container command in
[Testing](testing.md#engine-tests-in-a-throwaway-container).

Closing the window, File > Exit and every other normal quit stop the services and keep the data. A start in
progress is waited for first, which can take minutes on a first image build, so the process can outlive the
window. The wait is bounded to 10 minutes. If Docker has not finished by then, a dialog says so and the app exits.
The Docker command it was waiting for keeps running, and the services may stay or come up with the photo folder
mounted read-only: run `./start.sh --stop`. The same command is needed after a crash, a forced kill or, on Windows,
a system shutdown or log-off, because Electron emits no quit event for those.

Starting a new analysis while a cancel is still finishing is refused with "An analysis is already running", and the
window shows that message instead of staying at "Starting...". Deleting a library is refused while the services are not
up in this session, with a message that says to start an analysis first, because its stored data can only be removed
through the API. Both are tested with a fake server only.
