"""MatterMatch command-line orchestration."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence, TextIO

from . import __version__
from .decoder import ZXingCppDecoder
from .diagnostics import Diagnostics, image_label
from .inventory import InventoryError, load_inventory
from .matching import ProcessingResult, process_images
from .output import OutputError, write_csv

EXIT_OK = 0
EXIT_USAGE_OR_INVENTORY = 2
EXIT_OUTPUT = 3
EXIT_IMAGE = 4
EXIT_STRICT_COUNT = 5


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
    parser.add_argument("--output", default="-", metavar="PATH", help="CSV destination (default: stdout)")
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
    parser.add_argument("images", nargs="+", metavar="IMAGE", help="image filename(s); directories are not expanded")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    decoder=None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return its documented shell status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    diagnostics = Diagnostics()

    try:
        inventory = load_inventory(args.inventory)
    except InventoryError:
        diagnostics.error(f"inventory {image_label(args.inventory)} is invalid or unreadable")
        diagnostics.write(err)
        return EXIT_USAGE_OR_INVENTORY

    try:
        active_decoder = decoder if decoder is not None else ZXingCppDecoder()
    except Exception:
        # A decoder factory failure is treated like an image failure;
        # serialization still emits a header.
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
        )

    output_failed = False
    try:
        write_csv(result.rows, args.output, stdout=out)
    except OutputError:
        diagnostics.error(f"output {image_label(args.output)} could not be written")
        output_failed = True

    diagnostics.write(err)
    if output_failed:
        return EXIT_OUTPUT
    if result.image_failure:
        return EXIT_IMAGE
    if result.strict_count_mismatch:
        return EXIT_STRICT_COUNT
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
