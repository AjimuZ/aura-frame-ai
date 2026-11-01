# Risks

Date: 2026-11-01. Owner of all items: the project owner, unless development assigns otherwise.

## Blockers

None for the documentation. P00.5 needs the owner's authorization to commit.

## Risk register

| ID | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R1 | The repository has no commits, so changes cannot be reverted | High | First commit before any development (P00.5) |
| R2 | 15.8 GB of system memory, with Docker limited to 8.2 GB by default | High | `.wslconfig` setting, a warning in `start.sh`, stages that never overlap |
| R3 | The default model is not yet tested on this PC, and its margin is an estimate | High | Bake-off in P03, with three smaller options ready |
| R4 | AI aesthetic judgment is weak. In the published benchmark the best model reaches 26.5% where experts reach 68.9% on the strict task | High | Comparisons in both orders, human final decision, wording that says "proposes" |
| R5 | With Codex, faces in photos reach OpenAI, and account settings decide how data is treated | High | Off by default, opt-in per library, a clear warning, documented account check |
| R6 | The community quantization `GSQ-RCO` has no published vision evaluation | Medium | Bake-off measures vision directly, with a fallback from a different family |
| R7 | The tournament may be slow: a 30 to 50 minute estimate for `balanced` | Medium | Presets, resume, verdict cache. P03 measures it, then budgets are tuned |
| R8 | Q-ReAlign's gain over Q-Align is reported by its authors only | Medium | Evaluate against the benchmark in P04, with Q-Align in reserve |
| R9 | Docker cannot restrict the Codex sidecar to OpenAI domains | Medium | The sidecar holds only anonymous copies, and its tools are off |
| R10 | Slow file access through the Windows bind mount on large collections | Medium | Working copies on the Linux volume mean originals are read once. Documented WSL option |
| R11 | Model and image sizes: about 35 GB on disk (estimate) | Low | Documented. 660 GB free on the reference PC |
| R12 | Fast-moving projects: flags and model names may change | Low | Pinned digests and versions, with checks in the roadmap |
| R13 | The benchmark dataset is gated, and its stated license is inconsistent (Apache-2.0 in the metadata, CC-BY-NC in the access terms) | Low | Local evaluation only, never redistributed, and owner accepts the terms |
| R14 | Electron 33.4.11 is out of support, and it renders AI text and runs on the host outside the containers | High | Upgrade first (P01.6). Moving 12 major versions may break things, and the checks in P01 and P02 cover this |
| R15 | Building the Windows app in a container is documented upstream, but not tried on this PC | Medium | Host Node.js fallback. Decided in P02.5 |
| R16 | The Electron main process needs the Docker CLI on the host, and recreating `api` when the folder changes takes time | Low | No-op when the folder is unchanged. Never during a run. Clear message if Docker is unavailable |

## Assumptions

- The tool is for personal and portfolio use. If it becomes commercial, licenses need a new review.
- One user on one computer. No multi-user or remote access.
- Git Bash is available for `start.sh` on Windows, and is present on the reference PC. WSL is not a target
  for the first delivery, because the window is a Windows program.
- "Electron" means a window on the host, not a window inside a container
  ([ADR 0009](decisions/0009-electron-desktop-shell.md)).

## Open questions

| ID | Question | Current assumption |
| --- | --- | --- |
| Q1 | Is the direct OpenAI API flow still wanted next to Codex? | Removed, see [ADR 0007](decisions/0007-retire-openai-quality-mode.md) |
| Q2 | Default size of the final selection? | 25 |
| Q3 | Language of AI explanations and of the UI? | Follows a setting, Italian and English first |
| Q4 | Keep separate libraries? | Yes, replacing the per-person profiles |
| Q5 | Is the pixel title font wanted, or only the icons? | Icons yes, font decided in P06 |
