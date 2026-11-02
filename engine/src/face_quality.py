"""Face quality assessment WITHOUT identity recognition.

This module detects whether a clear, well-placed, front-facing face is present and derives
interpretable quality proxies (size, centrality, clarity, eyes-visible, frontality,
occlusion). It uses MediaPipe Face Detection and its six coarse keypoints (eyes, nose,
mouth, ears) only.

It NEVER builds face embeddings, NEVER compares faces against references, and NEVER
attempts to identify or re-identify who is in the photo. See P1.10 in the project plan.
"""

import contextlib
import os
import sys
from typing import Any

import cv2
import numpy as np

import mediapipe as mp

from .utils import Photo, read_working_bgr

_mp_face = mp.solutions.face_detection
_detector: Any = None


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


# Lazily builds a single shared face detector, warming it up under stderr suppression so its
# one-time C++ init logs (XNNPACK/absl/feedback-manager) never reach the console. Reusing one
# detector across all photos also avoids the per-image cost of the original per-call context.
def _get_detector(min_conf: float) -> Any:
    global _detector
    if _detector is None:
        with _suppress_native_stderr():
            _detector = _mp_face.FaceDetection(
                model_selection=1, min_detection_confidence=min_conf
            )
            _detector.process(np.zeros((64, 64, 3), dtype=np.uint8))
    return _detector

# MediaPipe FaceDetection keypoint indices (relative coordinates).
_RIGHT_EYE, _LEFT_EYE, _NOSE, _MOUTH, _RIGHT_EAR, _LEFT_EAR = range(6)


# Computes face-quality metrics for a photo using MediaPipe face detection only. It reads the
# upright working copy made at ingest, never the original; without one the photo is unchanged.
def score_faces(photo: Photo, config: dict[str, Any]) -> Photo:
    image = read_working_bgr(photo, config)
    if image is None:
        return photo

    height, width = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    min_conf = float(config.get("face", {}).get("min_detection_confidence", 0.5))

    result = _get_detector(min_conf).process(rgb)

    detections = result.detections or []
    photo.number_of_faces = len(detections)
    photo.face_detected = photo.number_of_faces > 0
    photo.group_photo = photo.number_of_faces >= 2

    if not photo.face_detected:
        return photo

    # Use the largest detected face as the main subject (no identity matching).
    main = max(detections, key=lambda d: _box_area(d, width, height))
    box = main.location_data.relative_bounding_box
    face_w = max(box.width * width, 1.0)
    face_h = max(box.height * height, 1.0)

    # Face size relative to the frame; a larger face suits a headshot/main photo.
    area_ratio = (face_w * face_h) / float(width * height)
    photo.face_size_score = float(np.clip(area_ratio * 600.0, 0.0, 100.0))

    # How centered the face is horizontally and vertically.
    cx = box.xmin + box.width / 2.0
    cy = box.ymin + box.height / 2.0
    offset = abs(cx - 0.5) + abs(cy - 0.45)
    photo.face_center_score = float(np.clip(100.0 - offset * 160.0, 0.0, 100.0))

    # Clarity from local sharpness within the face region (local face blur).
    x1 = max(int(box.xmin * width), 0)
    y1 = max(int(box.ymin * height), 0)
    x2 = min(int((box.xmin + box.width) * width), width)
    y2 = min(int((box.ymin + box.height) * height), height)
    region = image[y1:y2, x1:x2]
    if region.size > 0:
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        photo.face_clarity_score = float(np.clip(sharpness / 150.0 * 100.0, 0.0, 100.0))

    _keypoint_metrics(photo, main)
    photo.face_quality_score = _composite(photo, config)

    # Flag (do not hard-reject here) overly crowded group photos.
    max_allowed = int(config["filters"]["max_group_faces_allowed"])
    if photo.number_of_faces > max_allowed:
        photo.reject_reason = photo.reject_reason or "crowded_group"

    return photo


# Derives eyes-visible, head-pose, and occlusion proxies from the six face keypoints.
def _keypoint_metrics(photo: Photo, detection: Any) -> None:
    kps = detection.location_data.relative_keypoints
    if len(kps) < 6:
        # No keypoints: assume frontal-ish with mild uncertainty.
        photo.eyes_visible_score = 60.0
        photo.occlusion_score = 40.0
        return

    re, le = kps[_RIGHT_EYE], kps[_LEFT_EYE]
    nose, mouth = kps[_NOSE], kps[_MOUTH]
    rear, lear = kps[_RIGHT_EAR], kps[_LEFT_EAR]

    eye_dx = abs(le.x - re.x) + 1e-6
    eye_cx = (le.x + re.x) / 2.0
    eye_cy = (le.y + re.y) / 2.0

    # Yaw proxy: nose horizontal offset from the eye midpoint, normalized by eye spacing.
    yaw = (nose.x - eye_cx) / eye_dx
    photo.head_yaw = float(np.clip(yaw * 90.0, -90.0, 90.0))

    # Pitch proxy: nose vertical position between eyes and mouth (0.5 ~ frontal).
    span = abs(mouth.y - eye_cy) + 1e-6
    pitch = (nose.y - eye_cy) / span - 0.5
    photo.head_pitch = float(np.clip(pitch * 90.0, -90.0, 90.0))

    # Eyes-visible proxy: both eyes well separated and roughly level => clearly visible.
    level = 1.0 - min(abs(le.y - re.y) / eye_dx, 1.0)
    photo.eyes_visible_score = float(np.clip((min(eye_dx * 6.0, 1.0) * 0.6 + level * 0.4) * 100.0, 0.0, 100.0))

    # Occlusion heuristic: a strongly asymmetric ear-to-eye geometry suggests a turned/
    # partly hidden face. Higher score = less occlusion.
    rspan = abs(re.x - rear.x)
    lspan = abs(lear.x - le.x)
    asym = abs(rspan - lspan) / (max(rspan, lspan) + 1e-6)
    photo.occlusion_score = float(np.clip(100.0 - asym * 100.0, 0.0, 100.0))


# Combines the face proxies into a single interpretable 0-100 quality score.
def _composite(photo: Photo, config: dict[str, Any]) -> float:
    w = config.get("face", {})
    frontality = float(np.clip(100.0 - (abs(photo.head_yaw) + abs(photo.head_pitch)) * 0.8, 0.0, 100.0))
    score = (
        photo.face_size_score * float(w.get("size_weight", 0.25))
        + photo.face_center_score * float(w.get("centrality_weight", 0.20))
        + photo.face_clarity_score * float(w.get("clarity_weight", 0.25))
        + photo.eyes_visible_score * float(w.get("eyes_weight", 0.15))
        + frontality * float(w.get("frontality_weight", 0.15))
    )
    return float(np.clip(score, 0.0, 100.0))


# Returns the pixel area of a detection's bounding box.
def _box_area(detection: Any, width: int, height: int) -> float:
    box = detection.location_data.relative_bounding_box
    return (box.width * width) * (box.height * height)
