"""Ordered decoding, Matter parsing, and inventory joins."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .decoder import BarcodeDecoder, Detection, order_detections
from .diagnostics import Diagnostics
from .inventory import Inventory
from .matter import NonMatter, parse_matter_payload, derive_manual_code


@dataclass(frozen=True)
class OutputRow:
    qr_code: str
    descriptor: str
    pairing_code: str

    def __iter__(self):
        yield self.qr_code
        yield self.descriptor
        yield self.pairing_code

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.qr_code, self.descriptor, self.pairing_code)


@dataclass(frozen=True)
class ProcessingResult:
    rows: tuple[OutputRow, ...]
    image_failure: bool
    strict_count_mismatch: bool


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


def process_images(
    images: Sequence[str | Path],
    inventory: Inventory,
    decoder: BarcodeDecoder,
    diagnostics: Diagnostics,
    *,
    expected_qr_count: int | None = None,
    strict_count: bool = False,
) -> ProcessingResult:
    """Process images sequentially, preserving argument and geometric order."""
    rows: list[OutputRow] = []
    seen_text: set[str] = set()
    image_failure = False
    strict_mismatch = False

    for filename in images:
        try:
            detections = [_coerce_detection(item) for item in decoder.decode(filename)]
            ordered = order_detections(detections)
        except Exception:
            # Do not disclose native decoder details or image bytes.  One bad
            # input never prevents later files from being recovered.
            image_failure = True
            diagnostics.image_failure(filename)
            ordered = []

        valid_count = 0
        for ordinal, detection in enumerate(ordered, start=1):
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
            # complete Matter payload has parsed successfully.  Invalid text
            # (including overlong decoder output) must never affect duplicate
            # state or produce a second warning.
            if text in seen_text:
                diagnostics.duplicate(filename, ordinal, text)
            else:
                seen_text.add(text)
            valid_count += 1
            for payload in payloads:
                pairing_code = derive_manual_code(payload)
                descriptor = inventory.get(pairing_code)
                if descriptor is None:
                    diagnostics.unmatched(filename, ordinal, text)
                    rows.append(OutputRow(text, "", ""))
                else:
                    rows.append(OutputRow(text, descriptor, pairing_code))

        if expected_qr_count is not None and valid_count != expected_qr_count:
            diagnostics.count_mismatch(filename, valid_count, expected_qr_count)
            strict_mismatch = True

    return ProcessingResult(tuple(rows), image_failure, strict_mismatch and strict_count)


# Concise aliases for library callers.
match_images = process_images
match_detections = process_images
