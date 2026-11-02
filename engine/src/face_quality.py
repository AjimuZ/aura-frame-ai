"""People signals from the MediaPipe Face Landmarker, WITHOUT identity recognition.

For each photo this module takes the face count, the face box, the eyeBlinkLeft and eyeBlinkRight blendshapes and a
rough head pose, measures local sharpness inside the main face box, and derives a small `people_penalty` for closed
eyes or a blurred main face. A photo with no face has a penalty of zero.

Every landmark and blendshape is reduced to a few scalars inside `MediaPipeLandmarker.detect`; the 478-point mesh
never leaves it, and nothing here builds, compares or stores a face vector or tries to tell who is in the photo.
The model file comes from the models volume only (`models.face_landmarker.file`) and is never downloaded: when it
is missing the signals stay unknown and a structured error event names the command that fetches it on request.
"""

import contextlib
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from . import progress_events as events
from .model_loader import ModelUnavailable, locate_task_file
from .utils import Photo, read_working_bgr

MODEL_KEY = "face_landmarker"
FETCH_ID = "face-landmarker"
# The penalty is a small nudge on a 0-1 prescore, so no setting can make it larger than this.
PENALTY_CAP = 0.25
_BLINK_LEFT = "eyeBlinkLeft"
_BLINK_RIGHT = "eyeBlinkRight"
# Landmark indices of the 468-point face mesh used for the box and the pose proxy.
_RIGHT_EYE_OUTER, _LEFT_EYE_OUTER, _NOSE_TIP, _UPPER_LIP = 33, 263, 1, 13
_MIN_LANDMARKS = _LEFT_EYE_OUTER + 1


@dataclass(frozen=True)
class FaceReading:
    """The scalars kept from one detected face: nothing here can identify or re-identify a person.

    `box` is (xmin, ymin, xmax, ymax) as fractions of the image. `yaw` and `pitch` are rough degrees from the
    nose position against the eyes and mouth. A blink value is 0 for an open eye and 1 for a closed one, or None
    when the model gave no blendshape.
    """

    box: tuple[float, float, float, float]
    yaw: float
    pitch: float
    blink_left: float | None
    blink_right: float | None


class Landmarker(Protocol):
    """What score_faces needs from a landmarker: the readings of every face in an RGB image."""

    # Returns one FaceReading per detected face of an upright uint8 RGB image.
    def detect(self, rgb: np.ndarray) -> list[FaceReading]: ...


# Redirects the OS stderr file descriptor to null for the duration of the block. MediaPipe's
# TFLite/absl startup logs come from statically-linked C++ and ignore TF_CPP_MIN_LOG_LEVEL /
# GLOG_minloglevel, so fd redirection is the only reliable way to keep them off the console.
@contextlib.contextmanager
def _suppress_native_stderr() -> Any:
    sys.stderr.flush()
    saved_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_fd, 2)
        os.close(devnull_fd)
        os.close(saved_fd)


# Reduces the landmarks (x, y as image fractions) and the blink blendshapes of one face to a FaceReading, or None
# when the mesh is too short to hold the points used. The box is the extent of all points, clipped to the image.
# The yaw proxy is the nose offset from the eye midpoint over the eye spacing, and the pitch proxy is where the
# nose lies between the eye line and the upper lip (0.5 is frontal); both are rough and untuned.
def reading_from_landmarks(
    landmarks: Sequence[tuple[float, float]], blendshapes: Mapping[str, float]
) -> FaceReading | None:
    if len(landmarks) < _MIN_LANDMARKS:
        return None
    xs = [point[0] for point in landmarks]
    ys = [point[1] for point in landmarks]
    box = (
        float(np.clip(min(xs), 0.0, 1.0)),
        float(np.clip(min(ys), 0.0, 1.0)),
        float(np.clip(max(xs), 0.0, 1.0)),
        float(np.clip(max(ys), 0.0, 1.0)),
    )
    right_eye, left_eye = landmarks[_RIGHT_EYE_OUTER], landmarks[_LEFT_EYE_OUTER]
    nose, mouth = landmarks[_NOSE_TIP], landmarks[_UPPER_LIP]
    eye_dx = abs(left_eye[0] - right_eye[0]) + 1e-6
    eye_cx = (left_eye[0] + right_eye[0]) / 2.0
    eye_cy = (left_eye[1] + right_eye[1]) / 2.0
    yaw = (nose[0] - eye_cx) / eye_dx
    span = abs(mouth[1] - eye_cy) + 1e-6
    pitch = (nose[1] - eye_cy) / span - 0.5
    return FaceReading(
        box=box,
        yaw=float(np.clip(yaw * 90.0, -90.0, 90.0)),
        pitch=float(np.clip(pitch * 90.0, -90.0, 90.0)),
        blink_left=_blink(blendshapes.get(_BLINK_LEFT)),
        blink_right=_blink(blendshapes.get(_BLINK_RIGHT)),
    )


# Returns a blink score clipped to 0-1, or None when the value is missing or not a finite number.
def _blink(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        return None
    return float(np.clip(value, 0.0, 1.0))


class MediaPipeLandmarker:
    """The Face Landmarker tasks API in image mode, returning FaceReadings only."""

    # Opens the task bundle at `model_path`. Raises ModelUnavailable (dependency_missing, load_failed).
    def __init__(self, model_path: Path, max_faces: int, min_confidence: float) -> None:
        try:
            import mediapipe as mp  # noqa: PLC0415 - heavy and optional, so imported on first use
            from mediapipe.tasks import python as mp_python  # noqa: PLC0415
            from mediapipe.tasks.python import vision  # noqa: PLC0415
        except ImportError as exc:
            raise ModelUnavailable(
                MODEL_KEY, "dependency_missing", "the mediapipe package is not installed in this environment"
            ) from exc
        self._mp = mp
        try:
            with _suppress_native_stderr():
                options = vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                    running_mode=vision.RunningMode.IMAGE,
                    num_faces=max_faces,
                    min_face_detection_confidence=min_confidence,
                    output_face_blendshapes=True,
                    output_facial_transformation_matrixes=False,
                )
                self._landmarker = vision.FaceLandmarker.create_from_options(options)
                self.detect(np.zeros((64, 64, 3), dtype=np.uint8))  # warm-up keeps one-time init logs quiet
        except Exception as exc:  # The native loader raises many types; all mean the model cannot be used.
            raise ModelUnavailable(
                MODEL_KEY, "load_failed", f"the Face Landmarker model could not be loaded ({exc.__class__.__name__})"
            ) from exc

    # Returns one FaceReading per face found. The landmark mesh and the blendshape list are reduced to scalars here
    # and dropped, so no face vector leaves this method.
    def detect(self, rgb: np.ndarray) -> list[FaceReading]:
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = self._landmarker.detect(image)
        readings: list[FaceReading] = []
        for index, mesh in enumerate(result.face_landmarks or []):
            shapes = result.face_blendshapes[index] if index < len(result.face_blendshapes or []) else []
            blinks = {c.category_name: c.score for c in shapes if c.category_name in (_BLINK_LEFT, _BLINK_RIGHT)}
            reading = reading_from_landmarks([(p.x, p.y) for p in mesh], blinks)
            if reading is not None:
                readings.append(reading)
        return readings

    # Releases the native landmarker.
    def close(self) -> None:
        self._landmarker.close()


_UNSET: Any = object()
# The process-wide landmarker, None once loading failed, or _UNSET before the first use.
_shared: Any = _UNSET


# Returns a landmarker built from the configured model file. Raises ModelUnavailable when the model is not
# configured, missing from the models volume, invalid, or cannot be loaded; nothing is ever downloaded.
def load_landmarker(config: dict[str, Any]) -> MediaPipeLandmarker:
    path = locate_task_file(config, MODEL_KEY, FETCH_ID)
    face = config.get("face", {})
    return MediaPipeLandmarker(
        path,
        max_faces=max(1, int(face.get("max_faces", 8))),
        min_confidence=float(face.get("min_detection_confidence", 0.5)),
    )


# Returns the shared landmarker, loading it once per process, or None when it is unavailable. The first failure
# emits one structured `model_<code>` error event with the guidance to fetch the model; later calls stay quiet.
def shared_landmarker(config: dict[str, Any]) -> Landmarker | None:
    global _shared
    if _shared is _UNSET:
        try:
            _shared = load_landmarker(config)
        except ModelUnavailable as exc:
            _shared = None
            events.emit(
                {
                    "type": "log",
                    "level": "error",
                    "code": f"model_{exc.code}",
                    "model": exc.model,
                    "message": f"People signals are skipped, so no face penalty is applied: {exc.message}",
                }
            )
    return _shared


# Drops the shared landmarker so the next call loads it again. Used by tests and after a model change.
def reset_landmarker() -> None:
    global _shared
    closer = getattr(_shared, "close", None)
    if callable(closer):
        closer()
    _shared = _UNSET


# Returns the people penalty, 0 to `face.max_penalty` (never above PENALTY_CAP), on the same 0-1 scale as the
# prescore. Closed eyes weigh 0.6: the worst face counts, from the mean eyeBlink value `blink_start` (no penalty)
# up to `blink_full` (full). A blurred main face weighs 0.4: face clarity below `blur_clarity_below` (full at 0).
# No readings give exactly 0.0, and a blink value the model did not give adds nothing.
def people_penalty(readings: Sequence[FaceReading], main_clarity: float | None, config: dict[str, Any]) -> float:
    if not readings:
        return 0.0
    face = config.get("face", {})
    cap = float(np.clip(float(face.get("max_penalty", 0.10)), 0.0, PENALTY_CAP))
    start = float(face.get("blink_start", 0.45))
    full = max(float(face.get("blink_full", 0.80)), start + 1e-6)
    closures = [
        (reading.blink_left + reading.blink_right) / 2.0
        for reading in readings
        if reading.blink_left is not None and reading.blink_right is not None
    ]
    blink_term = max((float(np.clip((c - start) / (full - start), 0.0, 1.0)) for c in closures), default=0.0)
    below = float(face.get("blur_clarity_below", 40))
    blur_term = 0.0
    if main_clarity is not None and below > 0:
        blur_term = float(np.clip((below - main_clarity) / below, 0.0, 1.0))
    return float(np.clip(cap * (0.6 * blink_term + 0.4 * blur_term), 0.0, cap))


# Fills the face fields and the people penalty of a photo from the Face Landmarker, reading the upright working
# copy made at ingest, never the original. `landmarker` defaults to the shared one built from the models volume.
# Without a landmarker (model missing) or a working copy the photo is left unchanged and its penalty stays 0.
# A photo with no face has number_of_faces 0 and a penalty of 0.
def score_faces(photo: Photo, config: dict[str, Any], landmarker: Landmarker | None = None) -> Photo:
    landmarker = landmarker or shared_landmarker(config)
    if landmarker is None:
        return photo
    image = read_working_bgr(photo, config)
    if image is None:
        return photo

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    try:
        readings = landmarker.detect(rgb)
    except Exception as exc:  # An inference failure leaves this photo's people signals unknown, not invented.
        events.log(
            f"Face Landmarker failed on {photo.id[:12]} ({exc.__class__.__name__}); its people signals stay unknown.",
            "warning",
        )
        return photo

    photo.number_of_faces = len(readings)
    photo.face_detected = photo.number_of_faces > 0
    photo.group_photo = photo.number_of_faces >= 2
    photo.people_penalty = 0.0
    if not photo.face_detected:
        return photo

    # Use the largest detected face as the main subject (no identity matching).
    main = max(readings, key=_box_area)
    xmin, ymin, xmax, ymax = main.box
    area_ratio = max(xmax - xmin, 0.0) * max(ymax - ymin, 0.0)

    # Face size relative to the frame; a larger face suits a headshot/main photo.
    photo.face_size_score = float(np.clip(area_ratio * 600.0, 0.0, 100.0))

    # How centered the face is horizontally and vertically.
    offset = abs((xmin + xmax) / 2.0 - 0.5) + abs((ymin + ymax) / 2.0 - 0.45)
    photo.face_center_score = float(np.clip(100.0 - offset * 160.0, 0.0, 100.0))

    # Clarity from local sharpness within the face box (local face blur).
    clarity = _face_clarity(image, main.box)
    photo.face_clarity_score = clarity if clarity is not None else 0.0

    photo.head_yaw, photo.head_pitch = main.yaw, main.pitch
    photo.occlusion_score = 0.0  # The legacy ear-keypoint proxy has no Face Landmarker equivalent; not measured.
    eyes_open = _eyes_open_score(main)
    photo.eyes_visible_score = eyes_open if eyes_open is not None else 0.0
    photo.face_quality_score = _composite(photo, config, eyes_known=eyes_open is not None)
    photo.people_penalty = people_penalty(readings, clarity, config)

    # Flag (do not hard-reject here) overly crowded group photos.
    max_allowed = int(config["filters"]["max_group_faces_allowed"])
    if photo.number_of_faces > max_allowed:
        photo.reject_reason = photo.reject_reason or "crowded_group"

    return photo


# Returns the local sharpness of the face box as a 0-100 clarity score, or None when the box holds no pixels.
def _face_clarity(image: np.ndarray, box: tuple[float, float, float, float]) -> float | None:
    height, width = image.shape[:2]
    x1, y1 = max(int(box[0] * width), 0), max(int(box[1] * height), 0)
    x2, y2 = min(int(box[2] * width), width), min(int(box[3] * height), height)
    region = image[y1:y2, x1:x2]
    if region.size == 0:
        return None
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(np.clip(sharpness / 150.0 * 100.0, 0.0, 100.0))


# Returns how open the eyes are, 0-100 (100 = both open), or None when either blink value is unknown.
def _eyes_open_score(reading: FaceReading) -> float | None:
    if reading.blink_left is None or reading.blink_right is None:
        return None
    return float(np.clip((1.0 - (reading.blink_left + reading.blink_right) / 2.0) * 100.0, 0.0, 100.0))


# Combines the face proxies into a single interpretable 0-100 quality score, as a weighted mean (the configured
# weights sum to about 1). When the eyes are unknown their weight is left out of the mean, so an unknown value is
# not scored as good or bad.
def _composite(photo: Photo, config: dict[str, Any], eyes_known: bool = True) -> float:
    w = config.get("face", {})
    frontality = float(np.clip(100.0 - (abs(photo.head_yaw) + abs(photo.head_pitch)) * 0.8, 0.0, 100.0))
    parts = [
        (photo.face_size_score, float(w.get("size_weight", 0.25))),
        (photo.face_center_score, float(w.get("centrality_weight", 0.20))),
        (photo.face_clarity_score, float(w.get("clarity_weight", 0.25))),
        (frontality, float(w.get("frontality_weight", 0.15))),
    ]
    if eyes_known:
        parts.append((photo.eyes_visible_score, float(w.get("eyes_weight", 0.15))))
    total_weight = sum(weight for _, weight in parts)
    if total_weight <= 0:
        return 0.0
    score = sum(value * weight for value, weight in parts) / total_weight
    return float(np.clip(score, 0.0, 100.0))


# Returns the area of a reading's box as a fraction of the image.
def _box_area(reading: FaceReading) -> float:
    xmin, ymin, xmax, ymax = reading.box
    return max(xmax - xmin, 0.0) * max(ymax - ymin, 0.0)
