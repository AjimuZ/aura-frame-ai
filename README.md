<p align="center">
  <img src="apps/desktop/angular/src/assets/logo.svg" alt="AuraFrame AI" width="120" height="120" />
</p>

# AuraFrame AI

**Local AI photo curation: find the most beautiful photos in a large collection, on your own computer.**

AuraFrame AI analyzes a folder of photos and proposes the strongest ones, whatever the subject:
landscapes, travel, events, pets, architecture or portraits. It groups near-identical shots, compares the
candidates and explains its choices. You decide. Originals are never changed.

> **Status: the documentation describes the target product.** The code has not been migrated yet. The
> table below separates what exists from what is planned, and the [roadmap](docs/roadmap.md) shows the
> order of the work.

## Status

| Capability | Code today | Target |
| --- | --- | --- |
| Launch | Electron desktop app, no engine connected yet | Electron desktop app, services in Docker, `./start.sh` |
| Local AI | Ollama client | `llama-server`, Qwen3.8-27B by default |
| Cloud AI | Optional direct OpenAI flow | Optional Codex CLI, off by default |
| Subjects | Requires a face to reach the AI | Any subject |
| AI method | A 1 to 10 vote per photo | Pairwise tournament in both orders |
| Scoring | Classic metrics, CPU | Q-ReAlign and SigLIP 2, GPU |
| Selection | Six fixed slots | Diverse selection of configurable size |
| Interface | Soft dark theme | Minimal Pixel JRPG on the same palette |
| Originals read-only | Yes, by convention in code | Yes, enforced by a read-only mount |
| No telemetry, no identity recognition | Yes | Yes |

## How it will work

```text
folder -> ingest -> sanity gate -> quality and aesthetic scoring -> embeddings and content tags
       -> burst grouping -> shortlist -> pairwise AI tournament -> critique
       -> diverse selection -> your review -> export of copies and reports
```

Details and evidence: [Pipeline](docs/pipeline.md).

## Planned start

```bash
./start.sh --fetch-models    # explicit download, about 9.3 GB
./start.sh                   # starts the services and opens the desktop window
```

The window is an Electron app on your computer. Everything else runs in containers, and the services
that handle photos have no route to the internet. See [Operations](docs/operations.md).
**`start.sh` does not exist yet.**

## AI providers

| Provider | Photos leave the computer | Notes |
| --- | --- | --- |
| **Local model (default)** | No | Qwen3.8-27B on `llama-server`, chosen for a 12 GB GPU, to be confirmed by a test on the target PC |
| Codex CLI (optional) | Yes: resized copies with no metadata or names | Off by default, opt-in per library, faces in photos are visible to OpenAI |
| None | No | Local scoring only, the `fast` preset |

See [AI providers](docs/ai-providers.md).

## Running the current code

The legacy path until the container stack lands, needing Node.js only. Python runs only in
containers, so starting an analysis from the app fails with a clear message until P02.4. See
[Operations](docs/operations.md#running-the-current-code) and
[Testing](docs/testing.md#current-checks).

## Principles

- Originals are never modified, moved or deleted.
- Local by default. Cloud analysis happens only after explicit opt-in.
- No telemetry, no identity recognition, no sensitive inference.
- The AI judges the photograph, never a person's looks.
- Scores are real measurements. Unknown values stay unknown.
- The tool proposes, and you decide. Published evidence shows that AI is still far from expert judgment
  on aesthetics.

More in [Security and privacy](docs/security-privacy.md).

## Documentation

Start with the [documentation index](docs/README.md). Decisions are recorded in
[docs/decisions](docs/decisions/), and changes in the [changelog](CHANGELOG.md).

## License

`package.json` declares MIT, but no `LICENSE` file exists yet. See [roadmap](docs/roadmap.md) item P01.4.
