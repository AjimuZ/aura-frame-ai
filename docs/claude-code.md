# Claude Code

How the repository configures Claude Code, and which skills the owner approved. Verified on 2026-11-01.

## Project files

| File | Purpose |
| --- | --- |
| `CLAUDE.md` | Product context, non-negotiable rules, workflow and definition of done |
| `.claude/settings.json` | Permissions, plugin overrides and hooks |
| `.claude/agents/` | Five project subagents |
| `.claude/hooks/` | Local PowerShell hooks |
| `.claude/settings.local.json`, `.claude/logs/` | Machine-local files, ignored by Git |

This repository has no `.claude/skills/` or `.claude/commands/`, and must not get them.

## Permissions

- `defaultMode` is `default`, so normal prompts stay on. No allow rules exist.
- Direct access is denied to personal input, working, output, report and thumbnail directories, and to
  every `.env*` file, including `.env.example`.
- Every MCP tool is denied.
- Agent-driven commit, push, tag, Git cleanup, hard reset and restore are denied. The owner can still run
  these in a separate terminal.
- `disableSkillShellExecution` is on, so skills cannot run shell substitutions while loading.

## Hooks

| Hook | Runs | Effect |
| --- | --- | --- |
| `guard-tool-use.ps1` | Before Bash, PowerShell, Write, Edit | Denies direct writes outside the repository, access to protected paths, recursive deletion, destructive Git commands, and upload commands. Never approves anything |
| `format-changed-file.ps1` | After Write, Edit | Formats `.ts`, `.html`, `.scss` with the local Prettier, if present |
| `audit-change.ps1` | After changes and configuration changes | Logs content-free entries for key configuration files |
| `notify.ps1` | Permission or idle prompt | Generic Windows notification |

When the architecture moves to containers, the hook rules need a review (P02). That review follows.

### Hook review after the container move (P02, 2026-11-02)

Read: `.claude/settings.json` and the four scripts in `.claude/hooks/`. Nothing was changed: sessions cannot edit
`.claude/settings.json`, and the hook scripts are the owner's guard, so each change below is a proposal for the owner.

Still sound:

- `guard-tool-use.ps1` still denies writes outside the repository and to `img/`, `data/input_originali`,
  `data/working`, `data/output`, `data/reports` and `thumbnails/`. `start.sh` and a bare compose still write exports
  to `./data/output`, which stays protected. The hook still never approves anything.
- `format-changed-file.ps1` and `notify.ps1` touch nothing the container move changed.
- No hook contacts a network service, and none logs file content.

Gaps the containers opened, with the proposed change:

| Gap | Proposed change |
| --- | --- |
| The generated data (database, thumbnails, working copies, review sessions) now lives in the Docker volume `auraframe_data`, which no rule covers. `docker compose exec api ...`, `docker cp` or `docker run -v auraframe_data:...` can read it, and `docker volume rm`, `docker volume prune`, `docker system prune --volumes` or `docker compose down -v` can delete it, review decisions included | Deny `docker volume rm` and `prune`, `docker system prune`, `down` with `-v` or `--volumes`, and `docker cp`, in `guard-tool-use.ps1` |
| The app's exports and reports now sit in `exports/<library id>/` under the app's `userData` folder, outside the repository. Writes there are already denied (outside the repository), reads are not | Add a `Read` deny for that folder in `.claude/settings.json`, and the folder name to the protected shell tokens |
| `data/session-token`, written by `./start.sh --backend-only`, is a live session secret that no rule covers | Add `Read(/data/session-token)` to the deny list |
| The upload patterns do not include `docker push`, `docker login` or `npm publish` | Add them to the upload patterns |
| `curl` with `-d` is denied in a command line, which also blocks a typed request to the local gateway on `127.0.0.1`. A script file that does the same, such as `tests/integration/backend-run.sh`, is not inspected | None needed: keep the denial, and keep such requests in reviewed scripts run through `bash scripts/check.sh` |
| `audit-change.ps1` watches `CLAUDE.md`, `.claude/settings.json`, `package.json`, `engine/config.yaml` and `.gitignore`, but the trust boundary now also lives in `compose.yaml`, `compose.gpu.yaml`, `docker/`, `start.sh`, `scripts/check.sh`, `scripts/compose-policy.cjs` and `tests/integration/` | Add those paths to the monitored list |
| Every hook runs `powershell.exe`. On a Linux host, which `start.sh` now supports, the guard cannot start, and a hook that fails without exit code 2 does not block the call, so the guard is silently absent there | Run the guard with `pwsh` where it exists, or record that sessions on Linux run without the guard |
| The user's chosen photo folder can be anywhere on disk, so no fixed rule protects it | None possible with fixed paths: the read-only mount in the containers is what protects the originals |

## Subagents

| Agent | Scope | Edits |
| --- | --- | --- |
| `frontend-angular-electron` | Angular, Electron main and preload, typed IPC, design system | Yes |
| `python-vision-engine` | Engine, API, scoring, grouping, selection | Yes |
| `local-ai-inference` | Provider contract, llama-server, Codex CLI, prompts, schemas | Yes |
| `security-reviewer` | Read-only privacy, Electron, container and API review | No |
| `technical-writer` | Documentation | Documentation only |

## Approved skills

The owner asked on 2026-11-01 for the 10 best skills, with no repetition, installed. They are installed at
**user scope** in `~/.claude/skills`, so they are not part of this repository.

| Skill | Purpose | Source | Commit | Installs | Repository stars |
| --- | --- | --- | --- | --- | --- |
| `grilling` | Interview that stress-tests a plan | `mattpocock/skills` | `4ae2317` | 1.0M | 309,042 |
| `codebase-design` | Vocabulary for deep modules and seams | `mattpocock/skills` | `4ae2317` | 894K | 309,042 |
| `tdd` | Red-green test discipline | `mattpocock/skills` | `4ae2317` | 1.2M | 309,042 |
| `diagnosing-bugs` | Feedback-loop method for hard bugs | `mattpocock/skills` | `4ae2317` | 875K | 309,042 |
| `webapp-testing` | Playwright testing of local web apps | `anthropics/skills` (official) | `ca45fc4` | 195K | 186,215 |
| `security-and-hardening` | Threat modeling and secure defaults | `addyosmani/agent-skills` | `8bfcc1f` | 75K | 112,475 |
| `angular-developer` | Angular guidance | `angular/skills` (official) | `2a2b801` | 47K | 708 |
| `fastapi` | FastAPI guidance | `fastapi/fastapi` (official) | `45cb225` | 14K | 104,531 |
| `docker-compose-patterns` | Compose services, networks and health checks | `docker/skills` (official) | `34969c3` | 1.6K | 608 |
| `docker-build-strategies` | Dockerfile builds, size and non-root users | `docker/skills` (official) | `34969c3` | 1.4K | 608 |

Install counts are from [skills.sh](https://skills.sh/) on the install date.

### How they were chosen

1. Start from the leaderboard and from searches for this project's stack.
2. Keep one skill per function, skip anything already available here, and skip anything unrelated.
3. Trust known publishers only. Counts alone mean little: the leaderboard shows the same skill under many
   unknown owners, often with very high counts. Four of the ten have lower counts, because the official
   skill for this stack beats a more popular copy from an unknown author.
4. Read each skill and its scripts in an isolated copy before installing.
5. After installing, confirm the files are identical to the reviewed copy, and scan them for hidden
   characters and suspicious wording. All ten passed.

### Rejected

| Skill | Reason |
| --- | --- |
| `find-skills`, `code-review`, `skill-creator`, `pdf`, `pptx`, `docx`, `xlsx` | Already available here |
| `grill-me` | One-line alias of `grilling` |
| `web-design-guidelines` | Fetches its instructions at run time from an unpinned branch |
| `frontend-design` | Would conflict with `ajimu-pixel-ui`, the design language chosen for this project |
| `improve-codebase-architecture` | Its report loads Tailwind and Mermaid from public CDNs. `codebase-design` keeps the useful part |
| `agent-browser` | Needs a global CLI, and `webapp-testing` covers the need |
| `insecure-defaults` (Trail of Bits) | Published as a multi-agent plugin rather than a plain skill. Not a clean fit for this installer |
| `verification-before-completion`, `systematic-debugging`, `test-driven-development` | Duplicate `ajimu-conventions`, `diagnosing-bugs` and `tdd` |
| Copies of `superpowers` under unknown owners | Unverifiable publishers, with suspicious counts |
| Video, messaging, cloud-vendor and database skills | Unrelated to this project |

### Rules for using them

- `CLAUDE.md` and project documents take precedence over any skill. For example, the `fastapi` skill mentions
  OpenTelemetry, but this project allows no telemetry.
- `webapp-testing` needs Playwright, which is **not installed**. It is a dependency, so it needs approval
  when first used, in P06. The skill covers the Angular renderer under `ng serve`. The Electron window needs
  Playwright's own Electron API, which the skill does not cover.
- `docker-*` helper scripts only run `docker compose config` and `docker build`, and need P02 files.
- `tdd` asks to agree the test seams with the owner before writing tests.

### Reinstall

Run from outside the repository, with telemetry off:

```bash
export DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
npx skills add <owner/repo> -g -a claude-code -s <skill> -y --copy
```

The upstream installer warns that skills run with full agent permissions. After any update, review again
and update the commit column.
