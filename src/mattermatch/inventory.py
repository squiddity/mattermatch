"""Inventory CSV loading, validation, and explicit normalization helpers."""
from __future__ import annotations

from dataclasses import dataclass
import csv
import os
from pathlib import Path
import tempfile
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


@dataclass(frozen=True)
class InventoryCheck:
    """Bounded diagnostics from the canonical inventory check command."""

    valid: bool
    row_count: int
    diagnostics: tuple[str, ...]


def _bounded_add(
    messages: list[str],
    message: str,
    *,
    limit: int = 100,
    omission: str = "additional inventory diagnostics omitted",
) -> None:
    if len(messages) < limit:
        messages.append(message)
    elif len(messages) == limit:
        # Keep exactly one summary entry after the bounded detail list.
        messages.append(omission)


def check_inventory(source: str | Path | TextIO) -> InventoryCheck:
    """Validate a *canonical* inventory and return safe row-level reasons.

    Unlike :func:`load_inventory`, this command deliberately rejects visual
    separators and surrounding whitespace in codes.  Scanning remains
    backwards-compatible and continues to accept those forms in input files.
    """
    messages: list[str] = []
    row_count = 0
    seen: set[str] = set()
    handle, close = _open_inventory(source)
    try:
        try:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration:
                _bounded_add(messages, "row 1: inventory is empty")
                return InventoryCheck(False, 0, tuple(messages))
            if header and header[0].startswith("\ufeff"):
                header[0] = header[0][1:]
            if header != ["code", "descriptor"]:
                _bounded_add(messages, "row 1: header must be exactly code,descriptor")
            for row_number, row in enumerate(reader, start=2):
                if not row:
                    continue
                row_count += 1
                if len(row) != 2:
                    _bounded_add(messages, f"row {row_number}: expected exactly two fields")
                    continue
                raw_code = row[0]
                if (
                    raw_code != raw_code.strip()
                    or " " in raw_code
                    or "-" in raw_code
                    or len(raw_code) not in (11, 21)
                    or any(char not in "0123456789" for char in raw_code)
                ):
                    _bounded_add(messages, f"row {row_number}: code is not canonical 11/21 ASCII digits")
                    continue
                if not verhoeff_validate(raw_code):
                    _bounded_add(messages, f"row {row_number}: code has an invalid Verhoeff checksum")
                    continue
                if raw_code in seen:
                    _bounded_add(messages, f"row {row_number}: duplicate canonical code")
                    continue
                seen.add(raw_code)
        except csv.Error:
            _bounded_add(messages, "inventory: malformed CSV")
        except UnicodeError:
            _bounded_add(messages, "inventory: input is not valid UTF-8")
    except InventoryError as exc:
        _bounded_add(messages, str(exc))
    finally:
        if close:
            handle.close()
    return InventoryCheck(not messages, row_count, tuple(messages))


def _column_order(columns: str | tuple[str, str] | list[str]) -> tuple[str, str]:
    if isinstance(columns, str):
        values = tuple(part.strip() for part in columns.split(","))
    else:
        values = tuple(columns)
    if values not in (("code", "descriptor"), ("descriptor", "code")):
        raise InventoryError("columns must be exactly code,descriptor or descriptor,code")
    return values  # type: ignore[return-value]


def _repair_code(raw: str, *, pad_leading_zeroes: bool) -> tuple[str, bool]:
    """Return a checksum-gated canonical code and whether it was repaired."""
    if not isinstance(raw, str):
        raise InventoryError("code is not text")
    cleaned = raw.strip().replace(" ", "").replace("-", "")
    if not cleaned or any(char not in "0123456789" for char in cleaned):
        raise InventoryError("code must contain ASCII decimal digits")
    if len(cleaned) > 21:
        raise InventoryError("code is longer than 21 digits")
    if pad_leading_zeroes and len(cleaned) not in (11, 21):
        lengths = (11, 21)
    else:
        # An already canonical-length value is validated at that length; do
        # not reinterpret a complete code as a different-length repair.
        lengths = (len(cleaned),)
    candidates: list[str] = []
    for length in lengths:
        if len(cleaned) > length or length not in (11, 21):
            continue
        candidate = "0" * (length - len(cleaned)) + cleaned
        if verhoeff_validate(candidate):
            candidates.append(candidate)
    if len(candidates) == 0:
        if pad_leading_zeroes:
            raise InventoryError("no unambiguous Verhoeff-valid 11/21-digit candidate")
        # Keep the normal scanner-facing reason for non-padding operation.
        raise InventoryError("code must contain valid 11 or 21 digits")
    if len(candidates) > 1:
        raise InventoryError("ambiguous leading-zero repair (both canonical lengths validate)")
    repaired = candidates[0] != cleaned
    return candidates[0], repaired


def normalize_inventory_rows(
    source: str | Path | TextIO,
    *,
    columns: str | tuple[str, str] | list[str] = ("code", "descriptor"),
    has_header: bool = True,
    pad_leading_zeroes: bool = False,
) -> tuple[list[tuple[str, str]], tuple[str, ...]]:
    """Convert explicitly described input rows to canonical ``code,descriptor``.

    All rows are validated before anything is written.  The returned messages
    contain only row numbers/reasons, never code values or descriptors.
    """
    order = _column_order(columns)
    messages: list[str] = []
    repairs: list[str] = []
    output_rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    handle, close = _open_inventory(source)
    try:
        try:
            reader = csv.reader(handle, strict=True)
            row_iter = enumerate(reader, start=1)
            if has_header:
                try:
                    header_row_number, header = next(row_iter)
                except StopIteration:
                    raise InventoryError("row 1: input is empty") from None
                if header and header[0].startswith("\ufeff"):
                    header[0] = header[0][1:]
                if header != list(order):
                    raise InventoryError(
                        f"row {header_row_number}: header must be exactly {','.join(order)}"
                    )
            for row_number, row in row_iter:
                if not row:
                    continue
                if len(row) != 2:
                    _bounded_add(messages, f"row {row_number}: expected exactly two fields")
                    continue
                values = dict(zip(order, row))
                try:
                    code, repaired = _repair_code(
                        values["code"], pad_leading_zeroes=pad_leading_zeroes
                    )
                except InventoryError as exc:
                    _bounded_add(messages, f"row {row_number}: {exc}")
                    continue
                if repaired:
                    _bounded_add(
                        repairs,
                        f"row {row_number}: repaired leading zeroes",
                        omission="additional inventory repair diagnostics omitted",
                    )
                if code in seen:
                    _bounded_add(messages, f"row {row_number}: duplicate canonical code")
                    continue
                seen.add(code)
                output_rows.append((code, values["descriptor"]))
        except csv.Error:
            _bounded_add(messages, "input: malformed CSV")
        except UnicodeError:
            _bounded_add(messages, "input: not valid UTF-8")
    finally:
        if close:
            handle.close()
    if messages:
        raise InventoryError("\n".join(messages))
    return output_rows, tuple(repairs)


def write_normalized_inventory(
    rows: list[tuple[str, str]],
    destination: str | os.PathLike[str],
    *,
    stdout: TextIO | None = None,
) -> None:
    """Write canonical inventory CSV, atomically for file destinations."""
    import sys

    if stdout is None:
        stdout = sys.stdout
    if os.fspath(destination) == "-":
        try:
            writer = csv.writer(stdout, lineterminator="\n")
            writer.writerow(("code", "descriptor"))
            writer.writerows(rows)
            stdout.flush()
        except (OSError, UnicodeError, csv.Error) as exc:
            raise InventoryError("normalized inventory could not be written") from exc
        return
    target = Path(destination)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.mattermatch-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("code", "descriptor"))
            writer.writerows(rows)
            handle.flush()
        os.replace(temporary, target)
        temporary = None
    except (OSError, UnicodeError, csv.Error) as exc:
        raise InventoryError("normalized inventory could not be written") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


# Compatibility-friendly aliases.
read_inventory = load_inventory
normalize_inventory_code = normalize_code
