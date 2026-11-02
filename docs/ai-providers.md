# AI providers

Status: the contract and the local llama-server provider are **implemented** in `engine/src/providers`
(P03.1), tested against a fake server only. They are **not yet wired into the pipeline**, and no model has been
run. The Codex CLI provider is **implemented in the engine** since P05.2.1 (`engine/src/providers/codex.py`,
[ADR 0017](decisions/0017-codex-provider-gate-and-cap.md)) and tested against a fake CLI only; its transport to the
sidecar, `--provider codex`, `--codex-login` and the opt-in dialog are **planned** ([P05](roadmap.md)). The sidecar
image, config and volumes exist since P05.1 ([ADR 0016](decisions/0016-codex-sidecar-image.md)) but have not been
built or run, and nothing calls them yet. The pipeline still uses the legacy client
until [P03](roadmap.md) and [P04](roadmap.md) land. Decisions: [ADR 0003](decisions/0003-llama-cpp-runtime.md),
[ADR 0004](decisions/0004-default-local-model.md),
[ADR 0005](decisions/0005-codex-cli-provider.md).

## Provider contract

The pipeline depends on one small interface. Every provider receives anonymous, resized,
EXIF-free JPEG copies, never a path or file name.

```python
class VisionProvider(Protocol):
    def health(self) -> ProviderHealth: ...
    def compare(self, first: bytes, second: bytes, prompt: Prompt) -> Comparison: ...
    def critique(self, image: bytes, prompt: Prompt) -> Critique: ...
```

Contract:

- Copies have a long side of at most 1,024 px (smaller images are not enlarged) and are JPEG, quality 90.
  `prepare_copy` draws them again from decoded pixels after applying the orientation, so no EXIF, ICC
  profile, comment, file name or path is carried over. A provider refuses anything else (`check_copy`).
- Output is validated against a JSON Schema. Invalid output is retried a bounded number of times,
  then recorded as failed. It never stops the run.
- Timeouts, retries and call limits are configurable and always bounded.
- A failed provider degrades the run to the local prescore and says so in the UI.
- No method raises. A failure comes back as a result with `ok=False` and a short `error` code
  (`invalid_input`, `unreachable`, `timeout`, `model_missing`, `http_<status>`, `invalid_output`,
  `call_limit`, `degraded`; the Codex provider adds `opt_in_required`, `cli_failed`, `no_output` and
  `exchange_failed`). The error never holds model text, image data or a path.

Types (all in `engine/src/providers/contract.py`):

| Type | Fields |
| --- | --- |
| `Prompt` | `name`, `version`, `text`. The version is part of the verdict cache key |
| `ProviderHealth` | `reachable`, `model_available`, `message` (what to do next); `ready` is both |
| `Comparison` | `ok`, `winner` (`A`, `B`, `tie`), `confidence`, `reasons`, `note`, `provider`, `model`, `prompt_version`, `attempts`, `error` |
| `Critique` | `ok`, `strengths`, `weaknesses`, `caption`, `provider`, `model`, `prompt_version`, `attempts`, `error` |

The verdict schemas are in `engine/src/providers/schemas.py`, checked by a small in-repo validator (no
JSON Schema package is approved). The `note` is capped at 160 characters, since a schema cannot count
words. The prompts are in `prompts.py` (`compare-1`, `critique-1`).

Limits (`ai.local` in `engine/config.yaml`, built by `ProviderSettings.from_mapping`). Every value is clamped:

| Setting | Default | Hard bounds |
| --- | --- | --- |
| `timeout_seconds` (per attempt, on each read) | 120 | 0.25 to 600 |
| `max_retries` | 2 | 0 to 5 |
| `retry_delay_seconds` (times the attempt number) | 0.5 | 0 to 10 |
| `max_calls` (per run) | 1,000 | 1 to 20,000 |
| `max_consecutive_failures` | 3 | 1 to 20 |
| `max_tokens` | 512 | 64 to 2,048 |

`GuardedProvider` wraps any provider. After `max_consecutive_failures` failures in a row, or at `max_calls`,
it stops calling the provider and sets `status.degraded` with a reason for the UI. It also adopts the degraded
state of an inner provider that keeps its own `status`, such as the Codex provider at its cap. `compare_or_prescore`
returns a low-confidence verdict from the two prescores (source `prescore`) whenever a call fails, so the
tournament can go on. Choosing that path in the pipeline and showing the reason in the UI come with P04.

| Provider | Setting | Data leaves the computer | Default |
| --- | --- | --- | --- |
| Local model | `ai.provider: local` | No | **Yes** |
| Codex CLI | `ai.provider: codex` | Yes, anonymous resized copies | No, needs opt-in |
| None | `ai.provider: none` | No | Used by the `fast` preset |

## Local provider

### Recommended default for the reference PC

**Qwen3.8-27B, ISTA-DASLab GSQ-RCO build, `IQ2_XS` quantization, on `llama-server`.**

| Item | Value |
| --- | --- |
| Model | [`Qwen/Qwen3.8-27B`](https://huggingface.co/Qwen/Qwen3.8-27B), Apache-2.0 |
| Weights | [`ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF), file `Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf`, 8.4 GB |
| Vision projector | `mmproj-Qwen3.8-27B-BF16.gguf`, 0.9 GB |
| Download | About 9.3 GB in total, on explicit request only: `./start.sh --fetch-models`, pinned by commit and SHA-256 ([Operations](operations.md#model-files)) |

**Status: recommended, not yet run on this PC.** The bake-off in P03 confirms it or replaces it.

Why this model:

- It is the newest model family that fits. Larger siblings such as Qwen3.8-Flash-Next start at
  66 GB.
- The vendor reports RealWorldQA 85.9, against 80.3 for Qwen3.5-9B, the usual 12 GB pick. Both
  are vendor numbers. No independent aesthetic result exists for either.
- At 2.5 bits per weight, ISTA-DASLab reports zero-shot benchmarks at 100.3% of the full model.
  Reasoning benchmarks do lose points (LiveCodeBench 76.57 against 85.71), but the judge runs
  with reasoning off, so this should matter less here. That is an inference, the benchmarks
  are text only, and the bake-off tests it on images.
- `IQ2_XS` leaves the widest margin on a card that also runs the Windows desktop.

### Server settings

```text
llama-server
  --model /models/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf
  --mmproj /models/mmproj-Qwen3.8-27B-BF16.gguf
  --ctx-size 8192          two images plus prompt fit well below this
  --parallel 1             one comparison at a time
  --gpu-layers all
  --reasoning off          no thinking output in the verdict
  --sleep-idle-seconds 120 frees GPU memory between stages
  --host 0.0.0.0 --port 8080   reachable only on the internal network
```

Requests use `/v1/chat/completions` with images as base64 data URLs, `temperature: 0`, and
`response_format` with the verdict JSON Schema. `health()` reads `/health` and `/v1/models`. A 404 on a call
is `model_missing` and is not retried; a 5xx, 408, 425 or 429 is retried within the bound. All flags above are present in the local image
build 11960 (`--help` checked on 2026-11-01), except that `--gpu-layers all` comes from the
upstream server documentation.

### Memory fit

Estimated with published sizes and context costs (8K context costs about 0.5 GiB). These are
**lower bounds**, since they leave out compute buffers. The reference PC has about 10.77 GiB
free. P03 measures the real peak.

| Build | Weights | Context | Projector | Total | Margin |
| --- | --- | --- | --- | --- | --- |
| **Qwen3.8-27B `IQ2_XS`** | 7.84 GiB | 0.50 GiB | 0.87 GiB | **9.21 GiB** | **1.56 GiB** |
| Qwen3.8-27B `IQ2_S` | 8.62 GiB | 0.50 GiB | 0.87 GiB | 9.99 GiB | 0.78 GiB |
| Qwen3.8-27B `IQ3_XXS` | 9.40 GiB | 0.50 GiB | 0.87 GiB | 10.77 GiB | none |

System memory is the tighter limit. Docker has 8.2 GB by default. See
[Operations](operations.md#memory-on-windows).

### Bake-off

P03 runs these candidates on this PC, on the photography subset of the Visual Aesthetic
Benchmark, with both candidate orders:

| Candidate | Size with projector | Reason to test |
| --- | --- | --- |
| Qwen3.8-27B `IQ2_XS` | 9.3 GB | Recommended default |
| Qwen3.8-27B `IQ2_S` | 10.2 GB | Better scores, smaller margin |
| [Qwen3.5-9B](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) `Q5_K_M` | 7.5 GB | Fallback, likely faster |
| [Gemma 4 12B](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF) `Q4_K_M` | 7.1 GB plus projector | Previous default family |

Every candidate is pinned in the fetch manifest, so `./start.sh --fetch-models all` (or one model id) prepares
the bake-off files; see [Operations](operations.md#model-files) for the ids and file names.

Recorded for each: accuracy, stability across orders, seconds per comparison, peak GPU memory
and peak system memory. The winner is the accurate model that fits with at least 0.5 GiB free.
If speed differs a lot and accuracy does not, the faster model wins.

## Codex CLI provider

Uses the [Codex CLI](https://github.com/openai/codex) (Apache-2.0) as an alternative judge.

> **Photos leave the computer.** With this provider, resized copies, including any visible faces,
> go to OpenAI. File names, paths and EXIF do not.

### How it runs

An isolated sidecar runs one command per comparison:

```bash
codex exec \
  --ephemeral --skip-git-repo-check --sandbox read-only \
  --image a.jpg --image b.jpg \
  --output-schema /schemas/comparison.json \
  --output-last-message /tmp/verdict.json \
  "<versioned prompt>"
```

The sidecar config turns off every capability this job does not need:

```toml
web_search = "disabled"
history.persistence = "none"
analytics.enabled = false
feedback.enabled = false
otel.exporter = "none"
otel.trace_exporter = "none"
otel.metrics_exporter = "none"     # the upstream default is "statsig"

[features]
shell_tool = false
```

The file is `docker/codex/config.toml`, and `tests/codex-sidecar.test.cjs` keeps it identical to this block. The
entrypoint writes it into the `codex-home` volume on every start, so an older or edited copy there never applies.

Upstream notes that its Linux sandbox (`bwrap` and `seccomp`) can fail inside Docker. P05 checks
this. If it fails, the container stays the boundary (`--sandbox danger-full-access`), which is
acceptable only because the shell tool is off, and the container holds no originals.

### Sign-in

| Method | Command | Billing | Data handling, per upstream documentation |
| --- | --- | --- | --- |
| ChatGPT account | `./start.sh --codex-login` (runs `codex login --device-auth`) | Plan limits | Follows the workspace's access and retention settings |
| API key | Settings screen: Electron keeps the key encrypted, and passes it to `codex login --with-api-key` through stdin | API rates | Follows the API organization's retention and data-sharing settings |

Check your own account's data controls before opting in, because they decide whether content
may be used to improve models. Credentials stay in the `codex-home` volume. Upstream says to
treat `auth.json` like a password.

### Opt-in and limits

- The sidecar starts only with `--provider codex`, and each library must also confirm opt-in in
  the UI. The dialog states what is sent, and the number of calls the run will make.
- `codex.max_calls` caps each run (default 200). The tournament stops at the cap and ranks with
  the verdicts it has.
- `codex.model` is optional. Unset, the CLI's own default applies, so the project does not go
  stale when model names change. `model_reasoning_effort` defaults to `low` for this short task.
- Plan limits and API costs are the user's. Each call is a full agent turn, so it costs more than
  a bare image request. P05 measures this.

### Engine provider

**Status: implemented (P05.2.1), tested with a fake CLI only.** `CodexProvider` in `engine/src/providers/codex.py`
follows the provider contract ([ADR 0017](decisions/0017-codex-provider-gate-and-cap.md)):

- **Opt-in gate.** The provider is built with `opted_in=False` by default, and only the boolean `True` turns it on.
  Without it, every call returns `opt_in_required` before a copy is checked, a file is written or a process starts,
  and `health()` says opt-in is required. There is deliberately no opt-in key in `engine/config.yaml`.
- **Call cap.** Every CLI start, retries included, counts against `max_calls`. At the cap the provider refuses with
  `call_limit` and reports `degraded`; wrapped in `GuardedProvider(provider, settings.guard_settings())`, the guard
  reports it too, and `compare_or_prescore` returns prescore verdicts from then on.
- **One call.** A fresh private folder holds `a.jpg`, `b.jpg` (or `a.jpg` for a critique) and `schema.json`. The CLI
  runs there with relative names, `shell=False`, no stdin, its output discarded, and an environment of `PATH`,
  `HOME`, `CODEX_HOME` and the locale only. It writes `verdict.json`, which is validated against the verdict schema.
  The folder is removed whatever happens. A comparison adds one sentence to the prompt saying the first attachment
  is image A.
- **Failure.** The CLI missing is `unreachable` and not retried; a timeout, a non-zero exit (`cli_failed`), no
  verdict file (`no_output`) and invalid JSON or schema (`invalid_output`) are retried within `max_retries`.

Settings (`ai.codex` in `engine/config.yaml`, built by `CodexSettings.from_mapping`):

| Setting | Default | Hard bounds or rule |
| --- | --- | --- |
| `model` | empty (the CLI's default) | letters, digits, `.`, `_`, `:` and `-`, at most 64, not starting with `-` |
| `reasoning_effort` | `low` | `minimal`, `low`, `medium` or `high` |
| `timeout_seconds` (per CLI start) | 180 | 0.25 to 600 |
| `max_retries` | 1 | 0 to 5 |
| `retry_delay_seconds` (times the attempt number) | 2 | 0 to 10 |
| `max_calls` (CLI starts per run) | 200 | 1 to 20,000 |
| `max_consecutive_failures` (for the guard) | 3 | 1 to 20 |

Not yet decided or observed: how the `api` container hands a call to the sidecar (the default runner needs `codex`
in the same container, which `api` does not have; P05.2.2 and P05.3), the flags against the real CLI 0.166.0,
and whether the CLI's structured output accepts `maxLength` and `maxItems` in the verdict schemas.

## No provider

The `fast` preset, or `ai.provider: none`, skips stages 8 and 9. Ranking uses the prescore and the
final selection step, entirely on local scoring. This mode must always work.

## Sources

See [Technology choices](technology.md#sources).
