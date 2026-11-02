# Technology choices

Status: target stack, checked on 2026-11-01. Versions are pinned when each package is built. An
item marked "in use" exists in the code today.

## Stack

| Layer | Choice | License | Rationale | Alternatives rejected |
| --- | --- | --- | --- | --- |
| Services | Docker Compose, `start.sh` | Apache-2.0 | Owner decision, one command | Python launcher (`start.py`, no longer present): needs a local environment |
| Desktop shell | Electron (in use), on a supported major version | MIT | Owner decision. Hardened window, native dialogs and OS key store already exist | Browser interface: rejected by the owner, see [ADR 0009](decisions/0009-electron-desktop-shell.md) |
| Desktop build | `electron-builder` (in use), in the official builder image | MIT | No Node.js needed on the host | Host Node.js: kept as fallback and for development |
| Interface | Angular, standalone components, Signals (in use) | MIT | Seven screens exist and reach the backend through one interface | Rewrite in another framework: no benefit |
| Reverse proxy | nginx, unprivileged | BSD-2-Clause | Publishes one local port, while `api` stays on a network with no internet route | Publishing `api` directly: it would need an internet-capable network |
| API | FastAPI with SSE | MIT | Typed, native streaming for progress | Node backend: the engine is Python |
| Engine | Python 3.12 (in use) | PSF | Existing engine and ML ecosystem | |
| Storage | SQLite with WAL (in use) | Public domain | One file per library, resumable | Server database: unneeded |
| Decoding | Pillow, pillow-heif (in use) | MIT-CMU, BSD-3-Clause | HEIC support | |
| Quality and aesthetics | Q-ReAlign Mini via `transformers` >= 5.2 | Apache-2.0 | Same family as the strongest scorer in the published benchmark, GPU batches | See below |
| Embeddings, tags | SigLIP 2 base (`google/siglip2-base-patch16-224`) | Apache-2.0 | One model for grouping, diversity and zero-shot tags | See below |
| Faces | MediaPipe Face Landmarker, Tasks API | Apache-2.0 | Blink signals, no identity | Tasks API in use since P04.4c; the legacy `solutions` API is gone. Model pinned by object generation, see [ADR 0012](decisions/0012-people-signals-face-landmarker.md) |
| Local AI runtime | `llama-server`, CUDA image | MIT | No Ollama, GGUF quantizations, JSON Schema, sleeps when idle | See [ADR 0003](decisions/0003-llama-cpp-runtime.md) |
| Local AI model | Qwen3.8-27B GSQ-RCO `IQ2_XS` | Apache-2.0 | Best expected fit, see [AI providers](ai-providers.md) | See [ADR 0004](decisions/0004-default-local-model.md) |
| Cloud option | Codex CLI | Apache-2.0 | Owner decision | Direct OpenAI API flow, see [ADR 0007](decisions/0007-retire-openai-quality-mode.md) |

## Scorer: why not the alternatives

| Option | Result |
| --- | --- |
| **Q-ReAlign Mini** | Chosen. Apache-2.0, quality and aesthetic tasks, published 26.7 images/s on an RTX 4090. Its authors report an average SRCC of 0.879, above Q-Align's 0.869. |
| Q-ReAlign Lite or Pro | Better scores (0.889, 0.896) but over 10 GB of weights. Would not leave room on 12 GB. Can be a later option. |
| Q-Align | Best reward model in the independent benchmark. Heavier and older. It is the evidence for the family, while Q-ReAlign's gain is vendor-reported. |
| `pyiqa` (TOPIQ, MUSIQ, and others) | Rejected as a dependency. The toolbox uses the PolyForm Noncommercial license, which conflicts with the MIT license declared by this project. Useful for offline comparisons only. |
| LAION aesthetic predictor (in use) | Replaced. Needs weights absent from a clean install, and runs only on OpenCLIP vectors. |
| Laplacian blur, brightness, contrast (in use) | Replaced as scores. Punishes bokeh, fog and night scenes. |

## Embeddings: why not the alternatives

| Option | Result |
| --- | --- |
| **SigLIP 2** | Chosen. Apache-2.0, text and image, small (0.4B parameters). |
| DINOv3 | Possibly better on visual similarity, but under a custom license. |
| DINOv2 | Apache-2.0 and a good fallback for grouping, but gives no zero-shot tags without a second model. |
| OpenCLIP ViT-L/14 (in use) | Replaced. Older and larger, and runs on CPU in the current code. |

## Dependencies to remove

Removed by the packages in the [roadmap](roadmap.md): `open-clip-torch`, `timm`, `requests` (replaced by
`httpx`), `tqdm` (no import found), and `opencv-python` in favor of the headless build.

## Dependencies to upgrade

Electron stays, so it must stay supported. Upstream supports only the latest three major versions, and the
latest on 2026-11-01 is 45.2.1, with 44 and 43 still receiving releases. Done in P01.6: Electron is 45.2.1
(from 33.4.11) and `electron-builder` is 26.18.0 (from 25.1.8). Electron 45 requires Node.js 22.12 or newer
to install, and its npm package no longer fetches the binary at install: the first `electron` run downloads it.

## Sources

Checked on 2026-11-01.

### Curation evidence

- [Visual Aesthetic Benchmark, arXiv 2605.12684](https://arxiv.org/abs/2605.12684),
  [code](https://github.com/BakeLab/Visual-Aesthetic-Benchmark),
  [dataset](https://huggingface.co/datasets/BakeLab/Visual-Aesthetic-Benchmark)

### Scorers and embeddings

- [Q-ReAlign Lite model card](https://huggingface.co/q-future/Q-ReAlign-Lite-4B),
  [Q-ReAlign Mini](https://friendli.ai/models/q-future/Q-ReAlign-Mini-0.8B)
- [SigLIP 2 model card](https://huggingface.co/google/siglip2-base-patch16-224)
- [IQA-PyTorch, license and model list](https://github.com/chaofengc/IQA-PyTorch)
- [MediaPipe Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)

### Local models and runtime

- [Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [ISTA-DASLab GSQ-RCO quantizations](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF)
- [Qwen3.5-9B GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF),
  [Gemma 4 12B GGUF](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF)
- Secondary, for context only:
  [12 GB fit estimates](https://openclawdc.com/blog/qwen-3-8-27b-gsq-rco-quants/),
  [RTX 4090 speed tests](https://computingforgeeks.com/run-qwen3-8-27b-locally/),
  [local vision comparison](https://vdf.ai/blog/best-local-vision-models/),
  [12 GB guide](https://intelligibberish.com/articles/2026-03-17-local-ai-12gb-vram-guide-best-models-every-use-case/)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
  [Docker images](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)

### Electron

- [Electron release timelines and support policy](https://www.electronjs.org/docs/latest/tutorial/electron-timelines),
  [release list](https://releases.electronjs.org/releases.json)
- [electron-builder, multi-platform builds](https://www.electron.build/docs/features/multi-platform-build)
- [Playwright, Electron support](https://playwright.dev/docs/api/class-electron)

### Codex CLI

- [Repository](https://github.com/openai/codex),
  [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
  [command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
  [authentication](https://learn.chatgpt.com/docs/auth),
  [approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security),
  [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
