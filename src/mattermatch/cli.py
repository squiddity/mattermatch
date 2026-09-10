"""MatterMatch command-line orchestration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence, TextIO

from . import __version__
from .decoder import ZXingCppDecoder
from .diagnostics import Diagnostics, image_label
from .inventory import (
    InventoryError,
    check_inventory,
    load_inventory,
    normalize_inventory_rows,
    write_normalized_inventory,
)
from .matching import ProcessingResult, process_images
from .output import OutputError, write_csv, write_jsonl
from .verification import verify_pair

EXIT_OK = 0
EXIT_USAGE_OR_INVENTORY = 2
EXIT_OUTPUT = 3
EXIT_IMAGE = 4
EXIT_STRICT_COUNT = 5
EXIT_DIAGNOSTICS = 6
EXIT_VERIFY_FAIL = 7
# Descriptive aliases for integrations that prefer command-oriented names.
EXIT_ARTIFACT = EXIT_DIAGNOSTICS
EXIT_VERIFY = EXIT_VERIFY_FAIL


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mattermatch",
        description="Match Matter commissioning QR codes to an inventory CSV.",
    )
    parser.add_argument("--version", action="version", version=f"mattermatch {__version__}")
    parser.add_argument("--inventory", required=True, metavar="PATH", help="inventory CSV")
    parser.add_argument("--output", default="-", metavar="PATH", help="CSV/JSONL destination (default: stdout)")
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl"),
        default="csv",
        help="scan output format (default: csv)",
    )
    parser.add_argument(
        "--expected-qr-count",
        type=_nonnegative_int,
        metavar="N",
        help="warn when each image has a different number of valid Matter QR detections",
    )
    parser.add_argument(
        "--strict-count",
        action="store_true",
        help="return status 5 when --expected-qr-count does not match",
    )
    parser.add_argument(
        "--retry-preprocessing",
        action="store_true",
        help="opt-in to bounded grayscale/contrast/sharpness recovery retries",
    )
    parser.add_argument(
        "--diagnostics-dir",
        metavar="DIR",
        help="write sensitive annotated scan images and QR crop contact sheets",
    )
    parser.add_argument("images", nargs="+", metavar="IMAGE", help="image filename(s); directories are not expanded")
    return parser


def build_inventory_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mattermatch inventory",
        description="Validate or explicitly normalize an inventory CSV.",
    )
    commands = parser.add_subparsers(dest="inventory_command", required=True)
    check = commands.add_parser("check", help="validate canonical code,descriptor CSV")
    check.add_argument("path", metavar="PATH")

    normalize = commands.add_parser("normalize", help="convert an explicitly described CSV to canonical order")
    normalize.add_argument("input", metavar="INPUT")
    normalize.add_argument(
        "--columns",
        required=True,
        metavar="CODE,DESCRIPTOR",
        help="input column order: code,descriptor or descriptor,code",
    )
    header = normalize.add_mutually_exclusive_group()
    header.add_argument("--no-header", action="store_true", help="input has no header row")
    header.add_argument("--header", action="store_true", help="input has a header row (default)")
    normalize.add_argument(
        "--pad-leading-zeroes",
        action="store_true",
        help="repair only one unambiguous Verhoeff-valid 11/21-digit candidate",
    )
    normalize.add_argument("--output", required=True, metavar="OUTPUT", help="canonical CSV destination (or -)")
    return parser


def _inventory_command(
    argv: Sequence[str],
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    parser = build_inventory_parser()
    args = parser.parse_args(list(argv)[1:])
    diagnostics = Diagnostics()
    if args.inventory_command == "check":
        try:
            result = check_inventory(args.path)
        except InventoryError:
            diagnostics.error(f"inventory {image_label(args.path)} is unreadable")
            diagnostics.write(stderr)
            return EXIT_USAGE_OR_INVENTORY
        if result.valid:
            diagnostics.info(f"inventory {image_label(args.path)} is valid ({result.row_count} rows)")
            diagnostics.write(stderr)
            return EXIT_OK
        for message in result.diagnostics:
            diagnostics.error(message)
        diagnostics.write(stderr)
        return EXIT_USAGE_OR_INVENTORY

    # Normalize is intentionally never an in-place edit.  Rejecting an equal
    # path also covers the common accidental `-o input.csv` typo.
    if args.output != "-":
        try:
            if Path(args.input).resolve() == Path(args.output).resolve():
                diagnostics.error("input and output must be different files")
                diagnostics.write(stderr)
                return EXIT_USAGE_OR_INVENTORY
        except OSError:
            diagnostics.error("input and output paths could not be compared")
            diagnostics.write(stderr)
            return EXIT_USAGE_OR_INVENTORY
    try:
        rows, repairs = normalize_inventory_rows(
            args.input,
            columns=args.columns,
            has_header=not args.no_header,
            pad_leading_zeroes=args.pad_leading_zeroes,
        )
    except InventoryError as exc:
        for message in str(exc).splitlines():
            diagnostics.error(message)
        diagnostics.write(stderr)
        return EXIT_USAGE_OR_INVENTORY
    for message in repairs:
        diagnostics.info(message)
    try:
        write_normalized_inventory(rows, args.output, stdout=stdout)
    except InventoryError:
        diagnostics.error(f"output {image_label(args.output)} could not be written")
        diagnostics.write(stderr)
        return EXIT_OUTPUT
    diagnostics.info(f"normalized {len(rows)} rows to {image_label(args.output)}")
    diagnostics.write(stderr)
    return EXIT_OK


def build_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mattermatch verify-pair",
        description="Verify a Matter QR payload and manual pairing code semantically.",
    )
    parser.add_argument("--qr", required=True, metavar="MT:...", help="complete Matter QR payload")
    parser.add_argument("--pairing-code", required=True, metavar="CODE", help="11- or 21-digit manual code")
    parser.add_argument("--json", action="store_true", help="emit one machine-readable JSON object")
    parser.add_argument(
        "--chip-tool",
        metavar="PATH",
        help="optionally cross-check with an installed chip-tool executable",
    )
    return parser


def _verify_command(argv: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    parser = build_verify_parser()
    args = parser.parse_args(list(argv)[1:])
    result = verify_pair(args.qr, args.pairing_code, chip_tool=args.chip_tool)
    if args.json:
        print(json.dumps(result.as_json(), separators=(",", ":")), file=stdout)
    else:
        fields = result.as_json()
        for name in (
            "qr_setup_pin",
            "discriminator",
            "short_discriminator",
            "commissioning_flow",
            "expected_code_length",
        ):
            print(f"{name}={fields[name]}", file=stdout)
        if args.chip_tool:
            print(f"chip_tool={result.chip_tool_status}", file=stdout)
        print(f"{result.reason}", file=stderr)
        print("PASS" if result.passed else "FAIL", file=stdout)
    return EXIT_OK if result.passed else EXIT_VERIFY_FAIL


def _has_inventory_option(argv: Sequence[str]) -> bool:
    """Return whether ``argv`` has the scan-only inventory option.

    The first positional image is allowed to have the same spelling as an
    explicit command.  Seeing ``--inventory`` anywhere makes the legacy scan
    grammar unambiguous, including when argparse options follow that image.
    """
    return any(value == "--inventory" or value.startswith("--inventory=") for value in argv)


def _scan_output_aliases_input(output: str, inputs: Sequence[str]) -> bool:
    """Reject both path aliases and existing hard links before scan side effects.

    Atomic replacement does not protect an input chosen as the output path.
    Missing inputs remain recoverable image errors; other comparison errors
    propagate so that an unverifiable destination is never written.
    """
    if output == "-":
        return False
    target = Path(output)
    resolved_target = target.resolve()
    for source in inputs:
        path = Path(source)
        if resolved_target == path.resolve():
            return True
        try:
            if target.samefile(path):
                return True
        except FileNotFoundError:
            # A distinct new destination or missing image cannot alias an
            # existing file by inode; resolved path equality was checked above.
            continue
    return False


def main(
    argv: Sequence[str] | None = None,
    *,
    decoder=None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return its documented shell status."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    # Keep legacy scans unambiguous when a filename is literally a namespace
    # word.  An explicit command has no --inventory option, while a scan does;
    # for inventory, the second word must also be a known command.  Thus both
    # ``inventory --inventory inv.csv other.png`` and
    # ``verify-pair --inventory inv.csv`` remain ordinary scans.
    if raw_argv and raw_argv[0] == "inventory":
        if (
            len(raw_argv) >= 2
            and raw_argv[1] in {"check", "normalize"}
            and not _has_inventory_option(raw_argv)
        ):
            return _inventory_command(raw_argv, stdout=out, stderr=err)
    if raw_argv and raw_argv[0] == "verify-pair" and not _has_inventory_option(raw_argv):
        return _verify_command(raw_argv, stdout=out, stderr=err)

    parser = build_parser()
    # argparse's regular parser stops collecting a ``nargs='+'`` positional
    # after an option.  Intermixed parsing is needed for the legacy-valid
    # shape ``inventory --inventory inv.csv other.png`` where the first image
    # happens to be a namespace word.
    try:
        args = parser.parse_intermixed_args(raw_argv)
    except AttributeError:  # pragma: no cover - Python 3.10+ provides it.
        args = parser.parse_args(raw_argv)
    diagnostics = Diagnostics()

    try:
        inventory = load_inventory(args.inventory)
    except InventoryError:
        diagnostics.error(f"inventory {image_label(args.inventory)} is invalid or unreadable")
        diagnostics.write(err)
        return EXIT_USAGE_OR_INVENTORY

    try:
        collision = _scan_output_aliases_input(args.output, [args.inventory, *args.images])
    except (OSError, RuntimeError, ValueError):
        diagnostics.error("output and input paths could not be compared safely")
        diagnostics.write(err)
        return EXIT_OUTPUT
    if collision:
        diagnostics.error("scan output must be different from the inventory and every input image")
        diagnostics.write(err)
        return EXIT_USAGE_OR_INVENTORY

    try:
        active_decoder = decoder if decoder is not None else ZXingCppDecoder()
    except Exception:
        # A decoder factory failure is treated like an image failure;
        # serialization still emits a header/object stream.
        diagnostics.warning("decoder could not be initialized")
        result = ProcessingResult((), True, bool(args.strict_count and args.expected_qr_count is not None))
    else:
        result = process_images(
            args.images,
            inventory,
            active_decoder,
            diagnostics,
            expected_qr_count=args.expected_qr_count,
            strict_count=args.strict_count,
            retry_preprocessing=args.retry_preprocessing,
            diagnostics_dir=args.diagnostics_dir,
        )

    output_failed = False
    try:
        if args.format == "jsonl":
            write_jsonl(result.rows, args.output, stdout=out)
        else:
            write_csv(result.rows, args.output, stdout=out)
    except OutputError:
        diagnostics.error(f"output {image_label(args.output)} could not be written")
        output_failed = True

    diagnostics.write(err)
    if output_failed:
        return EXIT_OUTPUT
    if result.diagnostics_failure:
        return EXIT_DIAGNOSTICS
    if result.image_failure:
        return EXIT_IMAGE
    if result.strict_count_mismatch:
        return EXIT_STRICT_COUNT
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
