"""Exact CSV serialization and atomic destination writing."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import tempfile
import sys
from typing import Iterable, Mapping, TextIO

CSV_HEADER = ("qr_code", "descriptor", "pairing_code")


class OutputError(OSError):
    """CSV output could not be serialized or atomically installed."""


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


def write_csv(
    rows: Iterable[object],
    destination: str | os.PathLike[str],
    *,
    stdout: TextIO | None = None,
) -> None:
    """Write stdout directly or atomically replace a same-directory file."""
    if stdout is None:
        stdout = sys.stdout
    if os.fspath(destination) == "-":
        write_csv_stream(rows, stdout)
        return

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
            write_csv_stream(rows, handle)
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


atomic_write_csv = write_csv
