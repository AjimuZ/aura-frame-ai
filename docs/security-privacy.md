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
| `api` to internet | Data leak from a bug or a dependency | `backend` is an internal network with no route out. An exit check proves it |
| Image content to AI | Text inside a photo tries to steer the model | Output limited by JSON Schema, no tools available, and reasons are escaped when rendered |
| AI output to UI and reports | Script injection | Treated as untrusted text, always escaped, and never rendered as HTML |
| `api` to `codex` | Exposure of originals to a service with internet | Only anonymous, resized, EXIF-free copies cross this boundary, into a tmpfs. No path, name or database is mounted |
| `codex` to OpenAI | Image content leaves the computer | Off by default, opt-in per library, call limit, all Codex tools and telemetry off |
| `fetch-models` to internet | Tampered model files | Pinned repository revisions and SHA-256 checks. This service never sees photos |
| Model file loading | Code execution from model files | GGUF and safetensors only. Pickle-based formats are refused |

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
- HTML reports escape every value, including AI text (`engine/src/generate_report.py`).
- The engine makes no network calls except to the configured AI endpoint.
- The OpenAI flow is off by default and sends resized copies with EXIF stripped and orientation
  applied (`engine/src/cloud/openai_image_preparer.py`).
- Weights for the aesthetic predictor load with `weights_only=True`.
- A personal first name was hard-coded as the default library name, which broke the project rule
  against personal data in code. Fixed in P01.2.2; see [assessment](assessment.md#environment-and-repository).
  The `package.json` author field no longer holds a personal name, and `tests/personal-data.test.cjs`
  now fails on a user-home path in any tracked or unignored file, and on any word of at least four
  letters of the owner name read from `git config user.name` at test time. The name is never stored.
  The LICENSE copyright line is the only allowed exception.
