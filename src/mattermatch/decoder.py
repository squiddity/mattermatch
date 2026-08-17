"""Decoder protocol and the optional ZXing-C++ adapter."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 20_000
MAX_DECODED_TEXT = 4096


class ImageDecodeError(RuntimeError):
    """An image could not be safely loaded or decoded."""


class DecoderUnavailable(ImageDecodeError):
    """Optional native decoder dependencies are not installed."""


@dataclass(frozen=True)
class Detection:
    """A decoded symbol and its geometric position in the source image."""

    text: str
    position: Any = None

    @property
    def normalized_text(self) -> str:
        value = self.text if isinstance(self.text, str) else str(self.text)
        return value.strip()


BarcodeDetection = Detection


@runtime_checkable
class BarcodeDecoder(Protocol):
    def decode(self, filename: str | Path) -> Iterable[Detection]:
        """Decode all symbols in one image, retaining native result order."""


def _point_xy(point: Any) -> tuple[float, float] | None:
    if point is None:
        return None
    try:
        x, y = point.x, point.y
        return float(x), float(y)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        x, y = point[0], point[1]
        return float(x), float(y)
    except (IndexError, TypeError, ValueError):
        return None


def position_sort_key(detection: Detection) -> tuple[float, float, float, float, str]:
    """Return the deterministic (min_y, min_x, max_y, max_x, text) key."""
    position = detection.position
    points: list[tuple[float, float]] = []
    if position is not None:
        for name in ("top_left", "top_right", "bottom_left", "bottom_right"):
            point = _point_xy(getattr(position, name, None))
            if point is None:
                camel_name = "".join((part if index == 0 else part.title()) for index, part in enumerate(name.split("_")))
                point = _point_xy(getattr(position, camel_name, None))
            if point is not None:
                points.append(point)
        if not points:
            if isinstance(position, dict):
                candidates = [position.get(name) for name in ("top_left", "top_right", "bottom_left", "bottom_right")]
            else:
                try:
                    candidates = list(position)
                except TypeError:
                    candidates = []
            for candidate in candidates:
                point = _point_xy(candidate)
                if point is not None:
                    points.append(point)
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        bounds = (min(ys), min(xs), max(ys), max(xs))
    else:
        # A decoder/fake without geometry remains deterministic by text and
        # stable input order; infinities sort after all positioned symbols.
        bounds = (math.inf, math.inf, math.inf, math.inf)
    return bounds + (detection.normalized_text,)


def order_detections(detections: Iterable[Detection]) -> list[Detection]:
    return sorted(detections, key=position_sort_key)


sort_detections = order_detections


class ZXingCppDecoder:
    """Load each image once and decode QR symbols with zxing-cpp."""

    def __init__(
        self,
        *,
        max_pixels: int = MAX_IMAGE_PIXELS,
        max_dimension: int = MAX_IMAGE_DIMENSION,
    ) -> None:
        self.max_pixels = max_pixels
        self.max_dimension = max_dimension

    def decode(self, filename: str | Path) -> list[Detection]:
        try:
            from PIL import Image  # type: ignore
        except ImportError as exc:
            raise DecoderUnavailable("Pillow is not installed") from exc
        try:
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise DecoderUnavailable("NumPy is not installed") from exc
        try:
            import zxingcpp  # type: ignore
        except ImportError as exc:
            raise DecoderUnavailable("zxing-cpp is not installed") from exc

        try:
            # Image.open reads the format header but leaves pixel data lazy.
            # Check dimensions before load()/convert(), since compressed input
            # can otherwise expand into a hostile allocation first.
            image_context = Image.open(str(filename))
        except Exception as exc:  # Pillow exposes varied format/IO exceptions.
            raise ImageDecodeError("image could not be opened") from exc

        try:
            with image_context as image:
                try:
                    width, height = (int(image.size[0]), int(image.size[1]))
                except (AttributeError, IndexError, TypeError, ValueError) as exc:
                    raise ImageDecodeError("image has no usable dimensions") from exc
                if (
                    height <= 0
                    or width <= 0
                    or height > self.max_dimension
                    or width > self.max_dimension
                    or height * width > self.max_pixels
                ):
                    raise ImageDecodeError("image dimensions exceed safety limits")

                image.load()
                # ZXing accepts NumPy arrays.  Normalize all Pillow modes to a
                # compact RGB representation only after the checked load.
                rgb = image.convert("RGB")
                try:
                    rgb.load()
                    image_array = np.asarray(rgb)
                    if image_array.ndim != 3 or image_array.shape[:2] != (height, width):
                        raise ImageDecodeError("image has no usable pixel data")

                    formats = getattr(zxingcpp, "BarcodeFormat").QRCode
                    try:
                        results = zxingcpp.read_barcodes(image_array, formats=formats)
                    except TypeError:
                        results = zxingcpp.read_barcodes(image_array, formats)
                finally:
                    rgb.close()
        except ImageDecodeError:
            raise
        except Exception as exc:  # Native bindings expose varied exceptions.
            raise ImageDecodeError("image could not be decoded") from exc

        detections: list[Detection] = []
        for result in results or ():
            try:
                text = result.text
                position = result.position
            except AttributeError:
                continue
            if not isinstance(text, str):
                try:
                    text = str(text)
                except Exception:
                    continue
            # Preserve the complete native result.  parse_matter_payload
            # rejects text over MAX_DECODED_TEXT as malformed, allowing the
            # matching layer to issue exactly one bounded warning.
            detections.append(Detection(text, position))
        return detections


ZXingDecoder = ZXingCppDecoder
ZxingCppDecoder = ZXingCppDecoder
