# Security and privacy

Status: **target design**. The final section lists what the current code does today.

## Assets

| Asset | Sensitivity |
| --- | --- |
| Original photos | High: personal content, faces, EXIF with location |
| Derived data: thumbnails, working copies, vectors, reasons | High: reveals the content of the photos |
| Codex credentials | High: account access |
| File names and paths | Medium: may reveal names and places |

## Trust boundaries

| Boundary | Risk | Control |
| --- | --- | --- |
| Renderer to Electron main | A compromised renderer, for example through AI text, reaches the system | Sandbox, `contextIsolation`, no Node, one narrow typed bridge, validated IPC, strict CSP with no network access, navigation and new windows blocked |
| Electron main to `gateway` | Another program or a website calls the local API, including through DNS rebinding | Binds to `127.0.0.1`. Per-session bearer token known only to Electron main and `api`, never to the renderer. `Host` allowlist, no CORS |
| Electron main to Docker CLI | A crafted folder path alters a command | `spawn` with `shell: false`, folder validated as an existing directory, passed only as an environment value |
| Electron main to host files | An export reads outside the library, or overwrites an original | Native save dialog, source must resolve inside the library folder, an original cannot be the destination |
| `gateway` to `api` | Path traversal, unsafe input | Pydantic validation. Images served by photo ID only, never by path. The service sees only `/photos`, never a host path |
| `api` to photos | Damage to originals | Read-only mount. The container cannot write there, whatever the code does |
| `api` to exports | Copies written outside the exports folder, or into the photo folder | The only writable host mount. Main creates it apart from the photo folder and passes it only in the environment; the API copies there only, follows no symlink, and keeps each library in `/exports/<library id>/` |
| `api` to internet | Data leak from a bug or a dependency | `backend` is an internal network with no route out. An exit check proves it |
| Image content to AI | Text inside a photo tries to steer the model | Output limited by JSON Schema, no tools available, and reasons are escaped when rendered |
| AI output to UI and reports | Script injection | Treated as untrusted text, always escaped, and never rendered as HTML |
| `api` to `codex` | Exposure of originals to a service with internet | Only anonymous, resized, EXIF-free copies cross this boundary, into a tmpfs. No path, name or database is mounted |
| `codex` to OpenAI | Image content leaves the computer | Off by default, opt-in per library, call limit, all Codex tools and telemetry off |
| `fetch-models` to internet | Tampered model files | Pinned repository revisions, sizes and SHA-256 checks, https only, and a file is renamed into place only after it passes. This service never sees photos and cannot reach the other services (implemented in P03.2) |
| Model file loading | Code execution from model files | GGUF, safetensors and MediaPipe `.task` bundles (a ZIP of TFLite models, no pickle) only, by name and by first bytes (the `.task` entry arrived in P04.4c, with the Face Landmarker pinned by object generation id). Pickle-based formats are refused before any download (implemented in P03.2 for the downloads). The Q-ReAlign and SigLIP 2 loaders (P04.3, `engine/src/model_loader.py`, `engine/src/gpu_models.py`) read `.safetensors` only, check the header, take one plain directory name of the models volume (no hub identifier, no path), and pass `local_files_only`, `use_safetensors` and no remote code; a test scans the source for download calls. Real checkpoints have not been loaded yet |

## Desktop shell hardening

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, on a supported Electron major version.
- The bridge exposes a fixed set of typed methods, with no generic file, process or network access.
- The session token lives in the main process only. It is never sent to the renderer, written to a log, or
  passed as a command-line argument.
- Thumbnails reach the renderer only through the `afmedia` scheme, by photo ID.

## Container hardening

- Non-root users, `read_only` root filesystems where possible, `no-new-privileges`, all Linux
  capabilities dropped unless a service needs one.
- Images pinned by digest and rebuilt on purpose.
- No Docker socket in any container.
- Secrets never appear in images, arguments or logs.

## Privacy rules

- With `ai.provider: local` or `none`, nothing leaves the computer. This holds by network design,
  not only by code.
- Logs never contain file names or paths unless debug mode is on. Debug mode is local and off by
  default.
- GPS is never stored. Only capture time, camera make and model are kept from EXIF.
- No identity recognition, no face vectors, no identity-based grouping, no sensitive inference.
- No telemetry. This includes the Codex CLI, whose metrics exporter defaults to `statsig` and is
  turned off in the sidecar configuration.
- Personal photos are never used for automated tests. Tests use synthetic images.
- Deleting a library removes all its derived data.

## Residual risks

- Docker networks cannot limit `codex` to OpenAI domains. The sidecar can reach any host. The
  mitigation is that it holds only anonymous copies and its tools are off.
- With Codex, faces in the photos are visible to OpenAI. Metadata stripping does not change that.
- A process on the same computer can reach `127.0.0.1:8420`. The session token limits this, but the
  application does not defend against malware already running on the computer, which could also read the
  token from the process environment.
- Once this session's services are up, Electron main sends the token to that port until it stops them. A
  process that takes the port after the gateway dies could receive the token during that time.
- If Docker has not finished within 10 minutes of quitting, the app exits anyway and says so in a dialog.
  The Docker command it was waiting for keeps running, and the services may stay or come up with the photo
  folder mounted read-only until `./start.sh --stop`. Nothing stops them after a crash, a forced kill or, on
  Windows, a system shutdown or log-off, because Electron emits no quit event for those.
- Electron runs on the host, outside the container boundary. Its hardening and version support matter as
  much as the network design.
- Everything in the `data` volume is readable by anyone with Docker access on this computer.

See also [Risks](risks.md).

## Current code

Verified on 2026-11-01 by reading the source.

- Electron window hardened: `contextIsolation`, no `nodeIntegration`, sandbox, narrow typed
  preload bridge, validated IPC, blocked navigation.
- Electron is 45.2.1, a supported major, since P01.6 (it was 33.4.11, out of support). The window
  options, navigation lock, CSP and `afmedia` scheme are unchanged, and `tests/electron-hardening.test.cjs`
  fails if one leaves the source. That test reads source only: the behavior has not yet been observed in
  the running app on this version. The legacy key storage uses the `safeStorage` synchronous calls, which
  the 45.2.1 type definitions mark as deprecated but still provide; P05 removes that flow.
- Since P02.EXIT (2026-11-02) the navigation lock allows only the built `index.html` itself, or in development only
  the dev server's origin. It used to allow any `file://` URL and any URL that began with `http://localhost:4200`,
  such as a look-alike host. `tests/renderer-isolation.test.cjs` loads the compiled main process and preload against
  a stand-in for `electron` and checks the window options, the single `auraFrame` bridge of functions, a CSP with no
  network source, denied new windows and refused navigations. Chromium enforcing them in a real renderer is not
  observed.
- Electron main sends no request, and so never the session token, until `docker compose up` has succeeded in
  this session, and it stops at the next start or stop. Before that, reads return their fallbacks and writes are
  refused. Tested with a fake server and a fake Docker in `tests/analysis-http.test.cjs` and
  `tests/quit-handling.test.cjs`; not observed against a real Docker or a running Electron.
- `afmedia` requests a thumbnail from the API by photo ID, behind the same gate, and can never ask for an original
  (`apps/desktop/electron/media-proxy.ts`). The photo list reaches the window without `filePath` or `thumbnailPath`.
  Tested with a fake server in `tests/media-proxy.test.cjs`; not observed against the real API or in a running
  Electron.
- HTML reports escape every value, including AI text (`engine/src/generate_report.py`).
- The engine makes no network calls except to the configured AI endpoint.
- The OpenAI flow is off by default and sends resized copies with EXIF stripped and orientation
  applied (`engine/src/cloud/openai_image_preparer.py`).
- Weights for the aesthetic predictor load with `weights_only=True`.
- Since P03.2 (2026-11-02), model files for `vision` are downloaded only by `./start.sh --fetch-models`, in the
  `fetch-models` service: `egress` network only, profile `fetch`, the `models` volume as its only mount, non-root and
  read-only. Each file is pinned to a commit (or an object generation id), a size and a SHA-256, and only GGUF, safetensors or a `.task` bundle is accepted
  (`engine/src/model_fetch/`). `bash scripts/check.sh --integration` observed that it cannot reach `api` or
  `gateway`, sees no photos, data, exports or Docker socket, and can write the models volume. No real model file
  has been downloaded yet.
- Since P05.1 (2026-11-02), the `codex` sidecar exists in `compose.yaml` and `docker/codex/`: profile `codex`, so no
  plain `up` starts it; exactly `backend` and `egress`; a tmpfs at `/exchange` and the `codex-home` volume as its only
  mounts, so no photos, data, exports, models or Docker socket; uid 10001 on a read-only root; web search, history,
  analytics, feedback, telemetry and the shell tool off in its config, which the entrypoint rewrites on every start.
  `bash scripts/check.sh --compose` enforces these rules from the resolved configuration. Nothing calls it yet (P05.2),
  and its image has not been built, so none of this has been observed in a running container
  ([ADR 0016](decisions/0016-codex-sidecar-image.md)).
- A personal first name was hard-coded as the default library name, which broke the project rule
  against personal data in code. Fixed in P01.2.2; see [assessment](assessment.md#environment-and-repository).
  The `package.json` author field no longer holds a personal name, and `tests/personal-data.test.cjs`
  now fails on a user-home path in any tracked or unignored file, and on any word of at least four
  letters of the owner name read from `git config user.name` at test time. The name is never stored.
  The LICENSE copyright line is the only allowed exception.
