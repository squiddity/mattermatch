"""Ordered decoding, Matter parsing, and inventory joins."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .artifacts import ArtifactError, write_scan_artifacts
from .decoder import (
    BarcodeDecoder,
    Detection,
    merge_variant_detections,
    normalized_bounding_box,
    order_detections,
)
from .diagnostics import Diagnostics
from .inventory import Inventory
from .matter import NonMatter, derive_manual_code, parse_matter_payload


@dataclass(frozen=True)
class OutputRow:
    """One logical Matter payload and its optional inventory match.

    The first three fields intentionally remain the historical CSV contract.
    Source metadata is carried alongside them for JSON Lines serialization.
    """

    qr_code: str
    descriptor: str
    pairing_code: str
    source_image: str = ""
    detection_ordinal: int = 0
    bounding_box: tuple[float, float, float, float] | None = None
    status: str = "matched"
    raw_qr_code: str = ""

    def __iter__(self):
        # Keep tuple-like compatibility for existing CSV callers.
        yield self.qr_code
        yield self.descriptor
        yield self.pairing_code

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.qr_code, self.descriptor, self.pairing_code)

    def as_json_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "source_image": self.source_image,
            "detection_ordinal": self.detection_ordinal,
            "bounding_box": list(self.bounding_box) if self.bounding_box is not None else None,
            "qr_code": self.raw_qr_code if self.raw_qr_code else self.qr_code,
            "pairing_code": self.pairing_code,
            "descriptor": self.descriptor,
            "status": self.status,
        }
        return record


@dataclass(frozen=True)
class ProcessingResult:
    rows: tuple[OutputRow, ...]
    image_failure: bool
    strict_count_mismatch: bool
    diagnostics_failure: bool = False


def _coerce_detection(item: Any) -> Detection:
    if isinstance(item, Detection):
        return item
    if isinstance(item, str):
        return Detection(item)
    try:
        text = item.text
        position = getattr(item, "position", None)
        if not isinstance(text, str):
            text = str(text)
        return Detection(text, position)
    except AttributeError:
        pass
    if isinstance(item, (tuple, list)) and item:
        text = item[0]
        position = item[1] if len(item) > 1 else None
        return Detection(str(text), position)
    raise TypeError("decoder returned an invalid detection")


def _decode_for_scan(
    decoder: BarcodeDecoder,
    filename: str | Path,
    *,
    retry_preprocessing: bool,
    expected_qr_count: int | None,
) -> Iterable[Detection]:
    if not retry_preprocessing:
        return decoder.decode(filename)
    method = getattr(decoder, "decode_with_preprocessing", None)
    if callable(method):
        try:
            return method(filename, expected_qr_count=expected_qr_count)
        except TypeError:
            # Small injected decoders from downstream users may expose the
            # hint positionally or omit it altogether.
            try:
                return method(filename, expected_qr_count)
            except TypeError:
                return method(filename)
    variants = getattr(decoder, "decode_variants", None)
    if callable(variants):
        values = list(variants(filename))
        if (
            not values
            or isinstance(values[0], (Detection, str))
            or (
                isinstance(values[0], (tuple, list))
                and bool(values[0])
                and isinstance(values[0][0], str)
            )
        ):
            return values
        coerced_variants = [
            [_coerce_detection(item) for item in variant]
            for variant in values
        ]
        return merge_variant_detections(coerced_variants)
    # A decoder without a preprocessing hook remains backward-compatible: the
    # opt-in flag cannot manufacture photometric variants for an opaque plugin.
    return decoder.decode(filename)


def process_images(
    images: Sequence[str | Path],
    inventory: Inventory,
    decoder: BarcodeDecoder,
    diagnostics: Diagnostics,
    *,
    expected_qr_count: int | None = None,
    strict_count: bool = False,
    retry_preprocessing: bool = False,
    diagnostics_dir: str | Path | None = None,
) -> ProcessingResult:
    """Process images sequentially, preserving argument and geometric order."""
    rows: list[OutputRow] = []
    seen_text: set[str] = set()
    image_failure = False
    strict_mismatch = False
    diagnostics_failure = False

    for image_index, filename in enumerate(images, start=1):
        source_image = str(filename)
        try:
            detections = [
                _coerce_detection(item)
                for item in _decode_for_scan(
                    decoder,
                    filename,
                    retry_preprocessing=retry_preprocessing,
                    expected_qr_count=expected_qr_count,
                )
            ]
            ordered = order_detections(detections)
        except Exception:
            # Do not disclose native decoder details or image bytes.  One bad
            # input never prevents later files from being recovered.
            image_failure = True
            diagnostics.image_failure(filename)
            ordered = []

        if diagnostics_dir is not None:
            try:
                usable_geometry = write_scan_artifacts(
                    source_image,
                    image_index,
                    ordered,
                    diagnostics_dir,
                )
                if not usable_geometry:
                    diagnostics.artifact_no_geometry(filename)
            except ArtifactError:
                diagnostics_failure = True
                diagnostics.artifact_failure(filename)

        valid_count = 0
        for ordinal, detection in enumerate(ordered, start=1):
            raw_text = detection.text if isinstance(detection.text, str) else str(detection.text)
            text = detection.normalized_text
            try:
                payloads = parse_matter_payload(text)
            except NonMatter:
                diagnostics.non_matter(filename, ordinal, text)
                continue
            except Exception:
                diagnostics.malformed(filename, ordinal, text)
                continue

            # A duplicate warning/tracking entry is meaningful only after the
            # complete Matter payload has parsed successfully. Invalid text
            # must never affect duplicate state or produce a second warning.
            if text in seen_text:
                diagnostics.duplicate(filename, ordinal, text)
            else:
                seen_text.add(text)
            valid_count += 1
            box = normalized_bounding_box(detection.position)
            for payload in payloads:
                pairing_code = derive_manual_code(payload)
                descriptor = inventory.get(pairing_code)
                if descriptor is None:
                    diagnostics.unmatched(filename, ordinal, text)
                    # A valid unmatched QR still has a useful canonical code.
                    rows.append(
                        OutputRow(
                            text,
                            "",
                            pairing_code,
                            source_image,
                            ordinal,
                            box,
                            "unmatched",
                            raw_text,
                        )
                    )
                else:
                    rows.append(
                        OutputRow(
                            text,
                            descriptor,
                            pairing_code,
                            source_image,
                            ordinal,
                            box,
                            "matched",
                            raw_text,
                        )
                    )

        if expected_qr_count is not None and valid_count != expected_qr_count:
            diagnostics.count_mismatch(filename, valid_count, expected_qr_count)
            strict_mismatch = True

    return ProcessingResult(
        tuple(rows), image_failure, strict_mismatch and strict_count, diagnostics_failure
    )


# Concise aliases for library callers.
match_images = process_images
match_detections = process_images
