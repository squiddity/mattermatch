"""Decoder protocol and the optional ZXing-C++ adapter."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 20_000
MAX_DECODED_TEXT = 4096
MAX_RETRY_DETECTIONS = 512


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


def _position_points(position: Any) -> list[tuple[float, float]]:
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
    return points


def normalized_bounding_box(position: Any) -> tuple[float, float, float, float] | None:
    """Return an axis-aligned ``(min_x, min_y, max_x, max_y)`` box.

    ZXing exposes pixel coordinates and fake decoders commonly expose tuples;
    this deliberately normalizes both to one JSON-safe shape.  Missing or
    non-finite geometry is represented by ``None`` in source-aware output.
    """
    points = _position_points(position)
    if not points or any(not math.isfinite(value) for point in points for value in point):
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def position_sort_key(detection: Detection) -> tuple[float, float, float, float, str]:
    """Return the deterministic (min_y, min_x, max_y, max_x, text) key."""
    bounds = normalized_bounding_box(detection.position)
    if bounds is not None:
        min_x, min_y, max_x, max_y = bounds
        return (min_y, min_x, max_y, max_x, detection.normalized_text)
    # A decoder/fake without geometry remains deterministic by text and
    # stable input order; infinities sort after all positioned symbols.
    return (math.inf, math.inf, math.inf, math.inf, detection.normalized_text)


def order_detections(detections: Iterable[Detection]) -> list[Detection]:
    return sorted(detections, key=position_sort_key)


def _boxes_related(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Return whether two detections plausibly describe one physical QR."""
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    aw, ah = max(0.0, ax2 - ax1), max(0.0, ay2 - ay1)
    bw, bh = max(0.0, bx2 - bx1), max(0.0, by2 - by1)
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    if ix > 0 and iy > 0:
        return True
    # Preprocessing should not change geometry, but native bindings can move a
    # corner by a few pixels.  Only near-identical centers and dimensions are
    # accepted when boxes do not overlap; adjacent physical QRs must remain.
    if not aw or not ah or not bw or not bh:
        return False
    center_distance_x = abs((ax1 + ax2) - (bx1 + bx2)) / 2
    center_distance_y = abs((ay1 + ay2) - (by1 + by2)) / 2
    return (
        center_distance_x <= max(4.0, max(aw, bw) * 0.15)
        and center_distance_y <= max(4.0, max(ah, bh) * 0.15)
        and max(aw, bw) / min(aw, bw) <= 1.5
        and max(ah, bh) / min(ah, bh) <= 1.5
    )


def merge_variant_detections(variants: Sequence[Iterable[Detection]]) -> list[Detection]:
    """Merge only cross-variant repeats with equal text and nearby geometry.

    The first result for each physical symbol wins, preserving deterministic
    native ordering.  Results without geometry are intentionally not merged:
    without a position, two identical symbols cannot safely be distinguished.
    Duplicates in one decoder result remain duplicates by design.
    """
    merged: list[Detection] = []
    for variant_index, detections in enumerate(variants):
        current = list(detections)
        # Do not compare entries in the same native result: duplicate decoder
        # results are meaningful scan detections and must remain visible.
        prior_variant_detections = tuple(merged)
        for detection in current:
            text = detection.normalized_text
            bounds = normalized_bounding_box(detection.position)
            if variant_index:
                duplicate = False
                for prior in prior_variant_detections:
                    if prior.normalized_text != text:
                        continue
                    prior_bounds = normalized_bounding_box(prior.position)
                    if prior_bounds is not None and bounds is not None and _boxes_related(prior_bounds, bounds):
                        duplicate = True
                        break
                if duplicate:
                    continue
            merged.append(detection)
    return merged


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

    def _load_rgb(self, filename: str | Path):
        """Load one checked RGB image, rejecting hostile dimensions first."""
        try:
            from PIL import Image  # type: ignore
        except ImportError as exc:
            raise DecoderUnavailable("Pillow is not installed") from exc
        try:
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
                rgb = image.convert("RGB")
                rgb.load()
                if rgb.size != (width, height):
                    rgb.close()
                    raise ImageDecodeError("image has no usable pixel data")
                return rgb
        except ImageDecodeError:
            raise
        except Exception as exc:
            raise ImageDecodeError("image could not be decoded") from exc

    def _decode_rgb(self, rgb, *, max_results: int | None = None) -> list[Detection]:
        try:
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise DecoderUnavailable("NumPy is not installed") from exc
        try:
            import zxingcpp  # type: ignore
        except ImportError as exc:
            raise DecoderUnavailable("zxing-cpp is not installed") from exc
        try:
            image_array = np.asarray(rgb)
            if image_array.ndim != 3 or image_array.shape[:2] != rgb.size[::-1]:
                raise ImageDecodeError("image has no usable pixel data")
            formats = getattr(zxingcpp, "BarcodeFormat").QRCode
            try:
                results = zxingcpp.read_barcodes(image_array, formats=formats)
            except TypeError:
                results = zxingcpp.read_barcodes(image_array, formats)
        except ImageDecodeError:
            raise
        except Exception as exc:  # Native bindings expose varied exceptions.
            raise ImageDecodeError("image could not be decoded") from exc
        detections: list[Detection] = []
        for result in results or ():
            if max_results is not None and len(detections) >= max_results:
                break
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
            detections.append(Detection(text, position))
        return detections

    def decode(self, filename: str | Path) -> list[Detection]:
        """Decode the original image exactly once (the default scan path)."""
        rgb = self._load_rgb(filename)
        try:
            return self._decode_rgb(rgb)
        finally:
            rgb.close()

    def decode_with_preprocessing(
        self,
        filename: str | Path,
        *,
        expected_qr_count: int | None = None,
    ) -> list[Detection]:
        """Try a bounded, deterministic set of photometric variants.

        Geometry is unchanged.  The original image is always attempted first;
        subsequent grayscale/autocontrast, contrast, and sharpness variants are
        retained only long enough for one native decode.  Expected counts are
        intentionally not used for native early stopping: only the scan layer
        can distinguish validated Matter payloads from other QR results.
        """
        from PIL import ImageEnhance, ImageOps  # type: ignore

        rgb = self._load_rgb(filename)
        variants: list[list[Detection]] = []
        try:
            # Native results include arbitrary barcodes/QR text.  Never use
            # that raw count as an expected Matter count: a non-Matter QR in
            # the original can otherwise suppress every recovery attempt.
            variants.append(self._decode_rgb(rgb, max_results=MAX_RETRY_DETECTIONS))
            gray = rgb.convert("L")
            try:
                candidate = ImageOps.autocontrast(gray).convert("RGB")
            finally:
                gray.close()
            try:
                variants.append(self._decode_rgb(candidate, max_results=MAX_RETRY_DETECTIONS))
            finally:
                candidate.close()
            candidate = ImageEnhance.Contrast(rgb).enhance(1.5)
            try:
                variants.append(self._decode_rgb(candidate, max_results=MAX_RETRY_DETECTIONS))
            finally:
                candidate.close()
            candidate = ImageEnhance.Sharpness(rgb).enhance(2.0)
            try:
                variants.append(self._decode_rgb(candidate, max_results=MAX_RETRY_DETECTIONS))
            finally:
                candidate.close()
            return merge_variant_detections(variants)
        finally:
            rgb.close()


ZXingDecoder = ZXingCppDecoder
ZxingCppDecoder = ZXingCppDecoder
