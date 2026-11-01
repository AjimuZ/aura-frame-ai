# Requirements

Status: target requirements, accepted 2026-11-01 unless marked otherwise. The
[assessment](assessment.md) records how far the current code meets them.

## Purpose

AuraFrame AI finds the most beautiful photos in a large personal collection. It is a
general photo curation tool: it serves landscapes, travel, events, pets, architecture and
portraits equally. The person using it always has the final say.

## Functional requirements

| ID | Requirement |
| --- | --- |
| F1 | Analyze a folder of at least 2,000 photos in JPEG, PNG, WebP or HEIC. |
| F2 | Rank photos by photographic quality and visual appeal, whatever the subject. A photo without people is never penalized for that reason. |
| F3 | Group near-identical shots and bursts, propose the best one and keep the others available as alternates. |
| F4 | Propose a diverse selection of configurable size (default 25) plus Top 10, 25, 50 and 100 tiers. |
| F5 | Explain each finalist in plain language: strengths, weaknesses and content category. |
| F6 | Let the user accept, reject, replace and compare photos, and persist those choices. |
| F7 | Export the selection as copies with the original file names, plus CSV and HTML reports. |
| F8 | Keep separate libraries with isolated analysis data. |
| F9 | Offer selectable AI providers: local model (default), Codex CLI (optional), none. |
| F10 | Work completely with no AI provider, using local scoring only. |
| F11 | Resume after a crash or cancellation without repeating finished work on unchanged files. |
| F12 | Report progress live, and support pause, resume and cancel. |

## Delivery requirements

| ID | Requirement |
| --- | --- |
| D1 | The interface is an Electron desktop application. |
| D2 | Everything runs in Docker containers, except the desktop window. |
| D3 | One command starts it all: `./start.sh`. |
| D4 | The application does not use Ollama. |
| D5 | A recommended local vision model is the default on the reference hardware. |
| D6 | Model downloads happen only on explicit request, never as a side effect of starting. |

## Quality requirements

| ID | Requirement |
| --- | --- |
| Q1 | Originals are mounted read-only and are never modified, moved or deleted. |
| Q2 | With the local provider, no photo data leaves the computer, and the services handling photos have no route to the internet. |
| Q3 | Cloud analysis runs only after explicit opt-in, and receives only resized, metadata-free copies with anonymous names. |
| Q4 | No telemetry or external analytics. |
| Q5 | No face identity recognition, face embeddings or identity-based grouping. |
| Q6 | The AI judges the photograph. It never judges a person's looks, body, age or any sensitive characteristic. |
| Q7 | Scores shown in the interface are real measurements. Missing values are shown as unknown, never invented. |
| Q8 | The interface follows the [design system](design-system.md): keyboard usable, AA contrast, reduced motion honored. |
| Q9 | The default configuration runs on the reference hardware without running out of GPU memory. |

## Reference hardware

Measured on 2026-11-01. This is the machine the default configuration is tuned for.

| Item | Value |
| --- | --- |
| OS | Windows 11 Pro (10.0.26300) |
| CPU | Intel Core i5-14600KF, 14 cores, 20 threads |
| System memory | 15.8 GB |
| GPU | NVIDIA GeForce RTX 4070, 12,282 MiB, driver 617.42 |
| GPU memory used at idle | 1,253 MiB, so about 11,029 MiB free |
| Container runtime | Docker Desktop, engine 29.9.2, Compose v5.6.1 |
| Docker memory limit | 8.2 GB (default, no `.wslconfig`) |
| GPU in containers | Verified with `--gpus all`: `nvidia-smi` sees the RTX 4070 |

## Non-goals

- Identifying people or sorting a collection by person.
- Rating people, or any feature aimed at presenting a person to others.
- Editing, retouching or generating images.
- Hosting, sharing or syncing photos. The application serves only the local computer.
- Replacing human judgment. Published evaluations show current models are well below human
  experts at aesthetic choices (see [ADR 0006](decisions/0006-comparative-curation-pipeline.md)).
