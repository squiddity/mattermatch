"""Opt-in, bounded visual scan diagnostics."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
from typing import Sequence

from .decoder import Detection, normalized_bounding_box

MAX_ARTIFACT_CROPS = 64
MAX_ARTIFACT_DETECTIONS = 512
MAX_CROP_EDGE = 2048
CONTACT_TILE = 192
CONTACT_COLUMNS = 4


class ArtifactError(OSError):
    """A requested diagnostic artifact could not be safely written."""


def _safe_stem(source: str, image_index: int) -> str:
    digest = hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()[:12]
    return f"scan-{image_index:04d}-{digest}"


def _png_bytes(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _install_bytes(data: bytes, destination: Path) -> None:
    """Install bytes without ever replacing a concurrent artifact.

    A same-directory temporary is linked into place with ``link(2)``.  The
    destination creation is atomic and fails if another writer wins the race;
    only then do we inspect the winner to permit deterministic reuse of
    identical bytes.
    """
    temporary: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Unlike os.replace, hard-link creation is no-replace and atomic
            # on the same filesystem.  The temporary is unlinked below.
            os.link(temporary, destination)
        except FileExistsError:
            # A concurrent or prior identical run is safe to reuse.  Any
            # differing bytes (including an unreadable/non-regular target)
            # remain a collision and are never overwritten.
            try:
                if destination.is_file() and destination.read_bytes() == data:
                    return
            except OSError:
                pass
            raise ArtifactError(f"diagnostic artifact already exists: {destination.name}")
        except OSError as exc:
            raise ArtifactError("diagnostic artifact could not be installed") from exc
    except ArtifactError:
        raise
    except (OSError, ValueError) as exc:
        raise ArtifactError("diagnostic artifact could not be written") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _clamped_box(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = box
    left_i = max(0, min(width, int(left)))
    top_i = max(0, min(height, int(top)))
    right_i = max(0, min(width, int(right + 0.999)))
    bottom_i = max(0, min(height, int(bottom + 0.999)))
    if right_i <= left_i or bottom_i <= top_i:
        return None
    if right_i - left_i > MAX_CROP_EDGE:
        right_i = left_i + MAX_CROP_EDGE
    if bottom_i - top_i > MAX_CROP_EDGE:
        bottom_i = top_i + MAX_CROP_EDGE
    return left_i, top_i, min(width, right_i), min(height, bottom_i)


def write_scan_artifacts(
    source: str,
    image_index: int,
    detections: Sequence[Detection],
    destination: str | os.PathLike[str],
    *,
    max_pixels: int = 40_000_000,
    max_dimension: int = 20_000,
) -> bool:
    """Write an annotated image and QR contact sheet for one source image.

    Returns whether at least one detection had usable geometry.  The caller can
    warn when an image was decoded successfully but cannot be correlated to
    crops.  Payload text is never put in artifact names or labels.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError as exc:
        raise ArtifactError("Pillow is not installed") from exc
    try:
        context = Image.open(source)
    except Exception as exc:
        raise ArtifactError("source image could not be opened for diagnostics") from exc
    try:
        with context as original:
            width, height = int(original.size[0]), int(original.size[1])
            if (
                width <= 0 or height <= 0 or width > max_dimension or height > max_dimension
                or width * height > max_pixels
            ):
                raise ArtifactError("source image dimensions exceed safety limits")
            original.load()
            image = original.convert("RGB")
            try:
                annotated = image.copy()
                try:
                    draw = ImageDraw.Draw(annotated)
                    crops: list[tuple[int, object]] = []
                    usable = False
                    for ordinal, detection in enumerate(detections[:MAX_ARTIFACT_DETECTIONS], start=1):
                        box = normalized_bounding_box(detection.position)
                        if box is None:
                            continue
                        clamped = _clamped_box(box, width, height)
                        if clamped is None:
                            continue
                        usable = True
                        left, top, right, bottom = clamped
                        draw.rectangle((left, top, right - 1, bottom - 1), outline=(255, 0, 0), width=3)
                        # A short ordinal is sufficient to correlate with
                        # JSONL/CSV detection_ordinal and avoids payload leaks.
                        draw.text((left + 3, top + 3), str(ordinal), fill=(255, 0, 0))
                        if len(crops) < MAX_ARTIFACT_CROPS:
                            crop = image.crop(clamped)
                            # Shrink immediately so retaining up to 64 crops
                            # cannot retain dozens of multi-megapixel buffers.
                            crop.thumbnail((CONTACT_TILE, CONTACT_TILE), Image.Resampling.LANCZOS)
                            crops.append((ordinal, crop))
                    stem = _safe_stem(source, image_index)
                    directory = Path(destination)
                    _install_bytes(_png_bytes(annotated), directory / f"{stem}-annotated.png")
                finally:
                    annotated.close()

                # Contact sheet dimensions are bounded independently from the
                # source image.  A blank tile is retained when no geometry is
                # available, making the artifact pair predictable.
                tile_count = max(1, len(crops))
                rows = (tile_count + CONTACT_COLUMNS - 1) // CONTACT_COLUMNS
                sheet = Image.new("RGB", (CONTACT_COLUMNS * CONTACT_TILE, rows * (CONTACT_TILE + 20)), "white")
                try:
                    sheet_draw = ImageDraw.Draw(sheet)
                    for index, (ordinal, crop) in enumerate(crops):
                        try:
                            crop.thumbnail((CONTACT_TILE, CONTACT_TILE), Image.Resampling.LANCZOS)
                            x = (index % CONTACT_COLUMNS) * CONTACT_TILE + (CONTACT_TILE - crop.width) // 2
                            y = (index // CONTACT_COLUMNS) * (CONTACT_TILE + 20) + 20 + (CONTACT_TILE - crop.height) // 2
                            sheet.paste(crop, (x, y))
                            sheet_draw.text(((index % CONTACT_COLUMNS) * CONTACT_TILE + 3, (index // CONTACT_COLUMNS) * (CONTACT_TILE + 20) + 3), str(ordinal), fill=(0, 0, 0))
                        finally:
                            crop.close()
                    _install_bytes(_png_bytes(sheet), Path(destination) / f"{_safe_stem(source, image_index)}-contact.png")
                finally:
                    sheet.close()
                return usable
            finally:
                image.close()
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError("diagnostic artifact could not be generated") from exc
