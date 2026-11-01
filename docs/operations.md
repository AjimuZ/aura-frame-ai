# Operations

Status: **partly implemented** ([P02](roadmap.md)). `compose.yaml` and `start.sh` exist, and the desktop app's
analysis handlers call the stack over HTTP ([Bridge mapping](architecture.md#bridge-mapping)). Of the `start.sh`
contract below, the services, the checks, the token and the exit codes are implemented. The window,
`--fetch-models`, `--codex-login` and `--provider codex` are planned, and the script says so (see
[Not available yet](#not-available-yet)). To run the current code, see
[Running the current code](#running-the-current-code).

## Compose stack

```bash
AURAFRAME_API_TOKEN=<at least 16 characters> AURAFRAME_PHOTOS_DIR=<folder> \
  docker compose --profile local -f compose.yaml -f compose.gpu.yaml up -d --build
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `AURAFRAME_API_TOKEN` | none | Session token. `api` refuses to start without one |
| `AURAFRAME_PHOTOS_DIR` | `./data/no-photos` | Folder mounted read-only at `/photos` |
| `AURAFRAME_EXPORTS_DIR` | `./data/output` | Folder mounted at `/exports` |
| `AURAFRAME_PORT` | `8420` | Port of `gateway` on `127.0.0.1` |
| `AURAFRAME_API_TARGET` | `full` | Build stage of `api`. `core` leaves out PyTorch |
| `AURAFRAME_VISION_MODEL`, `AURAFRAME_VISION_MMPROJ` | The files in [AI providers](ai-providers.md#server-settings) | Model files in the `models` volume |

- Leave out `--profile local` to run without `vision`. Leave out `compose.gpu.yaml` on a host without a GPU.
- `vision` does not become healthy until its model files are in the `models` volume, which nothing fills
  yet (P03.2). Until then, run without the profile.
- On Linux, create the exports folder first, writable by uid 10001. Docker otherwise creates it as root.
  The API does not write there yet (P02.4.5).
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
  and `compose.gpu.yaml` only without `--cpu`. `--rebuild` first builds the images without the cache.
  Then the script asks the gateway for `/health` with the token. Failing either gives exit `3`.
- The token is 64 hex characters from `/dev/urandom`. Compose reads it from the environment, `curl` reads it
  from a configuration on stdin, and it is never printed. `--backend-only` also writes it to
  `data/session-token`, readable by the user only and ignored by Git.
- `--status`, `--logs [service]` and `--stop` only run the matching `docker compose` command.
  `--stop` keeps the volumes.

### Not available yet

These flags are valid, but their feature comes later. Each prints a "not available yet" message and exits
`1` without calling Docker or downloading anything:

| Flag | Planned in |
| --- | --- |
| `--fetch-models` | P03.2 |
| `--codex-login`, `--provider codex` | P05.2 |

The window is not launched either: its container build comes in P02.5. After a start, the script prints that,
leaves the services running, and `--stop` ends them. `--rebuild` rebuilds the images only. A window started
separately brings its own token, so its first run makes compose recreate `api` with that token.

## First start

```bash
./start.sh --fetch-models                 # about 9.3 GB for the default local model
./start.sh                                # then choose a folder in the window
```

The folder dialog is native. Electron mounts the chosen folder read-only in the `api` service.

## Desktop shell build

`start.sh` builds the Electron app into `release/` (Git-ignored) with `electron-builder`, in the official
`electronuserland/builder:wine` image on Windows and `electronuserland/builder` on Linux, pinned by digest.
This route rules out native runtime dependencies without prebuilds. The app's runtime dependencies have
none. The native modules in build tools (`lmdb`, `@parcel/watcher`) run only inside the container.

**Not yet tried on this PC.** If it fails, the fallback is to build on the host with Node.js:

```bash
npm ci && npm run build && npx electron .
```

## Development

```bash
./start.sh --backend-only     # services, and the session token in an ignored local file
npm start                     # Angular dev server and Electron, reading that token
npm run start:angular         # Angular alone, in a browser, with the mock API
```

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
| Exports, reports | `./data/output` | Delete the folder |
| Database, thumbnails, working copies | Volume `auraframe_data` | The app, per library, or `docker volume rm` |
| Model files | Volume `auraframe_models` | `docker volume rm` |
| Codex credentials | Volume `auraframe_codex_home` | `docker volume rm` |

## Troubleshooting

| Symptom | Cause | Action |
| --- | --- | --- |
| Window says the services are unavailable | Started outside `start.sh`, or a service failed | Run `./start.sh --status`, then `--logs` |
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

Starting an analysis from the window runs `docker compose up -d --wait` with the chosen folder, then calls the
API on `127.0.0.1` (`AURAFRAME_PORT`, default `8420`). Docker must be running, otherwise the app reports that the
services could not be started. Until a run starts the services, the app sends the API nothing and shows no
photos, including those of earlier runs; saving a review, exporting and clearing data report that the service is
not reachable. This path is tested with a fake server and a fake Docker only, and has not been run against the
real stack. Run the engine tests with the container command in
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
