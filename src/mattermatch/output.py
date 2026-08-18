"""Exact CSV and source-aware JSONL serialization with atomic destinations."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import tempfile
import sys
from typing import Iterable, Mapping, TextIO

CSV_HEADER = ("qr_code", "descriptor", "pairing_code")


class OutputError(OSError):
    """Output could not be serialized or atomically installed."""


def _row_values(row: object) -> tuple[str, str, str]:
    if isinstance(row, Mapping):
        return (str(row["qr_code"]), str(row["descriptor"]), str(row["pairing_code"]))
    values = tuple(row)  # type: ignore[arg-type]
    if len(values) != 3:
        raise OutputError("output row has the wrong number of fields")
    return tuple(str(value) for value in values)  # type: ignore[return-value]


def write_csv_stream(rows: Iterable[object], stream: TextIO | None = None) -> None:
    """Write the contract header and rows to an already-open text stream."""
    if stream is None:
        stream = sys.stdout
    try:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow(_row_values(row))
        stream.flush()
    except Exception as exc:
        # Iterables and row adapters are caller-supplied; normalize any
        # serialization failure so atomic callers can preserve their target.
        raise OutputError("CSV output could not be written") from exc


def _json_record(row: object) -> dict[str, object]:
    if hasattr(row, "as_json_record"):
        record = row.as_json_record()  # type: ignore[attr-defined]
        if isinstance(record, dict):
            return record
    if isinstance(row, Mapping):
        return {
            "source_image": str(row.get("source_image", "")),
            "detection_ordinal": int(row.get("detection_ordinal", 0)),
            "bounding_box": row.get("bounding_box"),
            "qr_code": str(row["qr_code"]),
            "pairing_code": str(row["pairing_code"]),
            "descriptor": str(row["descriptor"]),
            "status": str(row.get("status", "matched")),
        }
    values = _row_values(row)
    return {
        "source_image": "",
        "detection_ordinal": 0,
        "bounding_box": None,
        "qr_code": values[0],
        "pairing_code": values[2],
        "descriptor": values[1],
        "status": "matched" if values[1] else "unmatched",
    }


def write_jsonl_stream(rows: Iterable[object], stream: TextIO | None = None) -> None:
    """Write one deterministic, properly escaped JSON object per logical row."""
    if stream is None:
        stream = sys.stdout
    try:
        for row in rows:
            # Insertion order is deliberate: this is the documented stable
            # schema and makes output pleasant to inspect and diff.
            stream.write(json.dumps(_json_record(row), ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
        stream.flush()
    except Exception as exc:
        raise OutputError("JSONL output could not be written") from exc


def _write_atomic(
    writer,
    rows: Iterable[object],
    destination: str | os.PathLike[str],
) -> None:
    target = Path(destination)
    temporary: str | None = None
    try:
        # NamedTemporaryFile provides mode 0600 on normal POSIX platforms and
        # keeps temporary bytes in the target directory for an atomic replace.
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
            writer(rows, handle)
        os.replace(temporary, target)
        temporary = None
    except (OSError, OutputError, UnicodeError) as exc:
        raise OutputError("output file could not be created or replaced") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def write_csv(
    rows: Iterable[object],
    destination: str | os.PathLike[str],
    *,
    stdout: TextIO | None = None,
) -> None:
    """Write CSV stdout directly or atomically replace a same-directory file."""
    if stdout is None:
        stdout = sys.stdout
    if os.fspath(destination) == "-":
        write_csv_stream(rows, stdout)
        return
    _write_atomic(write_csv_stream, rows, destination)


def write_jsonl(
    rows: Iterable[object],
    destination: str | os.PathLike[str],
    *,
    stdout: TextIO | None = None,
) -> None:
    """Write JSON Lines stdout directly or atomically replace a file."""
    if stdout is None:
        stdout = sys.stdout
    if os.fspath(destination) == "-":
        write_jsonl_stream(rows, stdout)
        return
    _write_atomic(write_jsonl_stream, rows, destination)


atomic_write_csv = write_csv
atomic_write_jsonl = write_jsonl
