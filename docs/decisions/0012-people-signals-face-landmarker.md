# ADR 0012: People signals from the Face Landmarker, pinned by object generation

Status: accepted (2026-11-02).

## Context

The people signals used MediaPipe's legacy `solutions` face detection, which upstream stopped supporting in 2023
(assessment item D7). The target ([Pipeline](../pipeline.md#6-people-signals)) needs the face count, the
`eyeBlinkLeft` and `eyeBlinkRight` blendshapes and the face box, and the tasks API Face Landmarker is the supported
way to get them. It needs a model file, `face_landmarker.task` (3.6 MB). The project rules forbid downloading a model
unless the person asks, and the file must be pinned like the others ([ADR 0011](0011-pinned-model-download.md)).
Google publishes the file on Cloud Storage with only a `latest` alias and no numbered release path, and the fetcher
accepted Hugging Face commits and two formats only.

## Decision

- `engine/src/face_quality.py` uses the tasks API in image mode. Inside the adapter, the 478-point mesh and the
  blendshape list are reduced to a box, a rough head pose and two blink values (`FaceReading`); nothing else leaves
  it. No face vector is computed, compared or stored. The people penalty is at most `face.max_penalty` (0.10, hard
  cap 0.25): closed eyes weigh 0.6 and a blurred main face 0.4. A photo with no face has a penalty of exactly 0.
- The model file is read from the models volume by plain name (`models.face_landmarker.file`) and is never
  downloaded by the engine. When it is missing the signals stay unknown, the penalty stays 0, and one structured
  `model_file_missing` error event names `./start.sh --fetch-models face-landmarker`.
- The manifest pins the file by its Cloud Storage **generation id**, an immutable object version, in a URL of the form
  `...?generation=<id>`, with a SHA-256 computed once from a download the owner authorised and a size from the object
  metadata. The fetcher accepts such a URL only on an allow-listed host (`storage.googleapis.com`), over https, with
  `generation=<revision>` as its only query.
- `.task` joins `.gguf` and `.safetensors` as an accepted format. A task bundle is a ZIP archive of TFLite models
  with no pickle; the fetcher checks the ZIP header by content, as it does for the other formats.

## Alternatives

- **Keep the legacy API.** No download, but it is unsupported and cannot give blendshapes.
- **Pin the `latest` URL.** The SHA-256 would catch a change, but only after the download, and every later change
  upstream would break the fetch without notice.
- **Bundle the model in the image or the repository.** The image build must not download models, and a binary of
  unclear provenance in the repository is worse than a checked fetch.
- **Compute face embeddings for a better signal.** Forbidden: no identity or face vector may exist.

## Consequences

- Blink and face sharpness are measured only after the model is fetched; without it the people penalty is 0 and no
  photo is flagged as having a face (the sanity gate then adds `local_flag_face_not_detected`, a soft flag).
- The legacy ear-keypoint occlusion proxy is no longer measured (`occlusion_score` stays 0), and the pose proxies
  are rough and untuned.
- Evidence: `engine/tests/test_face_quality.py` and `engine/tests/test_model_fetch.py`, with a fake landmarker and
  fake files. The real model and the real `mediapipe` tasks API have not been run; that waits for P04.MEASURE.
