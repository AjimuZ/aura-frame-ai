# Operations

Status: **planned** ([P02](roadmap.md)). `start.sh` and `compose.yaml` do not exist yet. To run the
current code, see [Running the current code](#running-the-current-code).

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

This is the legacy path until P02 lands.

```powershell
npm install
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r engine/requirements.txt
npm start
```

On the reference PC this needs two fixes first: install Python 3.12, and recreate `.venv`, which
points to an interpreter from another user account. See
[assessment](assessment.md#environment-and-repository).
