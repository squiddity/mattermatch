"""Bounded, non-secret diagnostic formatting."""
from __future__ import annotations

import hashlib
import sys
from typing import TextIO


def fingerprint(text: str) -> str:
    """Short non-reversible identifier for a decoded payload."""
    return hashlib.sha256(text[:4096].encode("utf-8", "replace")).hexdigest()[:12]


def image_label(filename: object) -> str:
    value = str(filename).replace("\r", "?").replace("\n", "?")
    if len(value) > 160:
        value = value[:157] + "..."
    return value


class Diagnostics:
    """Collect diagnostics so stderr output remains ordered and testable."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def warning(self, message: str) -> None:
        self.lines.append(f"warning: {message}")

    def error(self, message: str) -> None:
        self.lines.append(f"error: {message}")

    def write(self, stream: TextIO | None = None) -> None:
        if stream is None:
            stream = sys.stderr
        for line in self.lines:
            print(line, file=stream)

    def image_failure(self, filename: object) -> None:
        self.warning(f"image {image_label(filename)} could not be read or decoded")

    def non_matter(self, filename: object, ordinal: int, text: str) -> None:
        self.warning(
            f"image {image_label(filename)} detection {ordinal}: non-Matter QR "
            f"({fingerprint(text)})"
        )

    def malformed(self, filename: object, ordinal: int, text: str) -> None:
        self.warning(
            f"image {image_label(filename)} detection {ordinal}: malformed Matter QR "
            f"({fingerprint(text)})"
        )

    def duplicate(self, filename: object, ordinal: int, text: str) -> None:
        self.warning(
            f"image {image_label(filename)} detection {ordinal}: duplicate QR "
            f"({fingerprint(text)})"
        )

    def unmatched(self, filename: object, ordinal: int, text: str) -> None:
        self.warning(
            f"image {image_label(filename)} detection {ordinal}: valid Matter QR "
            f"is not in inventory ({fingerprint(text)})"
        )

    def count_mismatch(self, filename: object, observed: int, expected: int) -> None:
        self.warning(
            f"image {image_label(filename)}: expected {expected} valid Matter QR "
            f"detections, observed {observed}"
        )
