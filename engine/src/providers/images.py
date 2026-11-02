"""Builds the anonymous JPEG copies a provider is allowed to see, and checks that copies conform.

A copy has a long side of at most 1,024 px and is JPEG at quality 90. It carries no EXIF, ICC
profile, comment, file name or path: it is drawn again from decoded pixels, so no metadata of
the source can be carried over.
"""

import io
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 1024
JPEG_QUALITY = 90

# Pillow refuses images this large, which guards against decompression bombs.
Image.MAX_IMAGE_PIXELS = 250_000_000


# Re-encodes an image (bytes or a path) as a metadata-free JPEG copy for a provider.
# The orientation is applied before the metadata goes, so the copy looks as the owner sees
# the photograph. Smaller images are not enlarged. Raises OSError when the source is unreadable.
def prepare_copy(source: bytes | str | Path) -> bytes:
    opened = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with Image.open(opened) as image:
        upright = ImageOps.exif_transpose(image)
        rgb = upright.convert("RGB")
    rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", rgb.size)
    clean.paste(rgb)
    buffer = io.BytesIO()
    clean.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


# Raises ValueError unless the bytes are a JPEG within the size bound and free of metadata.
# Providers call it on every input, so an unprepared original can never reach a model.
def check_copy(data: bytes) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != "JPEG":
                raise ValueError("The image copy must be a JPEG.")
            if max(image.size) > MAX_SIDE:
                raise ValueError(f"The image copy must be at most {MAX_SIDE} px on its long side.")
            if len(image.getexif()) or {"exif", "icc_profile", "comment"} & set(image.info):
                raise ValueError("The image copy must not carry metadata.")
    except OSError as exc:
        raise ValueError("The image copy cannot be decoded.") from exc
