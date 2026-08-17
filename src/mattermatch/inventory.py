"""Inventory CSV loading and strict manual-code normalization."""
from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from typing import TextIO

from .matter import verhoeff_validate


class InventoryError(ValueError):
    """The inventory is unreadable or violates its schema."""


@dataclass(frozen=True)
class Inventory:
    entries: dict[str, str]

    def get(self, code: str) -> str | None:
        return self.entries.get(code)


def normalize_code(code: str) -> str:
    """Normalize visual separators and validate a Matter manual pairing code."""
    if not isinstance(code, str):
        raise InventoryError("inventory code is not text")
    normalized = code.strip().replace(" ", "").replace("-", "")
    if len(normalized) not in (11, 21) or any(char not in "0123456789" for char in normalized):
        raise InventoryError("inventory code must contain 11 or 21 ASCII digits")
    if not verhoeff_validate(normalized):
        raise InventoryError("inventory code has an invalid Verhoeff checksum")
    return normalized


def _open_inventory(source: str | Path | TextIO):
    if hasattr(source, "read"):
        return source, False
    try:
        # newline="" is required by the csv module.  encoding utf-8-sig accepts
        # a BOM while rejecting malformed UTF-8 before any output is touched.
        return open(source, "r", encoding="utf-8-sig", newline=""), True
    except (OSError, UnicodeError) as exc:
        raise InventoryError("cannot read inventory") from exc


def load_inventory(source: str | Path | TextIO) -> Inventory:
    """Read and completely validate an inventory before returning any entries."""
    handle, close = _open_inventory(source)
    try:
        try:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise InventoryError("inventory is empty") from exc
            if header and header[0].startswith("\ufeff"):
                header[0] = header[0][1:]
            if header != ["code", "descriptor"]:
                raise InventoryError("inventory header must be exactly code,descriptor")
            entries: dict[str, str] = {}
            for row_number, row in enumerate(reader, start=2):
                # csv.reader represents a genuinely empty CSV line as [].
                if not row:
                    continue
                if len(row) != 2:
                    raise InventoryError(f"inventory row {row_number} must have two fields")
                try:
                    code = normalize_code(row[0])
                except InventoryError as exc:
                    raise InventoryError(f"invalid inventory code on row {row_number}") from exc
                if code in entries:
                    raise InventoryError(f"duplicate inventory code on row {row_number}")
                entries[code] = row[1]
            return Inventory(entries)
        except csv.Error as exc:
            raise InventoryError("malformed inventory CSV") from exc
        except UnicodeError as exc:
            raise InventoryError("inventory is not valid UTF-8") from exc
    finally:
        if close:
            handle.close()


# Compatibility-friendly aliases.
read_inventory = load_inventory
normalize_inventory_code = normalize_code
