"""Privacy-safe image preparation for OpenAI Quality Mode.

Originals are NEVER sent. For each photo this module makes a temporary, resized,
EXIF-stripped, anonymized JPEG copy, returns it as base64, and deletes the temp file.
The resize always preserves aspect ratio (no forced square, no crop, no stretch).
"""

import base64
import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from ..utils import ensure_dir


@dataclass
class PreparedImage:
    """A resized, metadata-free JPEG copy ready to send (base64 + final dimensions)."""

    b64: str
    width: int
    height: int


# This function creates a privacy-safe resized copy while preserving the original aspect
# ratio. It never forces a square or crops: thumbnail() scales the longest side to max_side
# and derives the other side from the original proportions.
def resize_preserve_aspect_ratio(image: Image.Image, max_side: int) -> Image.Image:
    image.thumbnail((max_side, max_side))
    return image


# Returns (and creates) the working subdirectory used for short-lived temp copies.
def temp_dir(work_dir: Path) -> Path:
    return ensure_dir(Path(work_dir) / "openai_tmp")


# Opens an original read-only, bakes EXIF orientation into pixels, converts to RGB, and
# returns an aspect-preserving copy bounded by max_side. The source EXIF is discarded.
def _load_oriented(path: str, max_side: int) -> Image.Image:
    with Image.open(path) as image:
        image.load()
        oriented = ImageOps.exif_transpose(image).convert("RGB")
    return resize_preserve_aspect_ratio(oriented, max_side)


# Encodes an image to JPEG bytes WITHOUT EXIF (Pillow does not copy EXIF unless asked).
def _encode_jpeg(image: Image.Image, jpeg_quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(jpeg_quality), optimize=True)
    return buffer.getvalue()


# Prepares one photo for sending: temp copy -> resize (aspect preserved) -> EXIF-free JPEG
# -> base64 -> delete temp. Returns a PreparedImage. Raises on unreadable images so the
# caller can mark the photo failed_openai_review without aborting the whole run.
def prepare_image(
    path: str,
    *,
    max_side: int,
    jpeg_quality: int,
    work_dir: Path,
    delete_temp: bool = True,
) -> PreparedImage:
    image = _load_oriented(path, max_side)
    jpeg_bytes = _encode_jpeg(image, jpeg_quality)

    # Write an anonymously-named temp copy (no original name, no path, no EXIF), then read
    # it back and delete it. This mirrors the "temporary copy" privacy contract and means
    # nothing identifiable is persisted beyond the request.
    tmp = temp_dir(work_dir) / f"{uuid.uuid4().hex}.jpg"
    try:
        tmp.write_bytes(jpeg_bytes)
        data = tmp.read_bytes()
    finally:
        if delete_temp and tmp.exists():
            tmp.unlink(missing_ok=True)

    return PreparedImage(
        b64=base64.b64encode(data).decode("ascii"),
        width=image.width,
        height=image.height,
    )


# Builds the data: URL the OpenAI vision API expects from a PreparedImage.
def data_url(prepared: PreparedImage) -> str:
    return f"data:image/jpeg;base64,{prepared.b64}"
