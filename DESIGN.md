# MatterMatch CLI Design

**Status:** Design only; no implementation is included.

## Intent

MatterMatch scans a caller-supplied set of image files for Matter commissioning QR
codes, derives each code's manual pairing code, and joins it to a small inventory
CSV. It is a deterministic, local-only batch CLI: shell glob expansion supplies
filenames, and MatterMatch never searches directories itself.

The first version is a production-oriented MVP: a small standard-library CLI,
ZXing-C++ for image decoding, and a narrowly scoped Python implementation of the
Project CHIP setup-payload rules. It does not need the full Project CHIP SDK or a
network connection.

## CLI contract

Recommended command shape:

```text
mattermatch --inventory INVENTORY.csv [--output OUTPUT.csv|-]
            [--expected-qr-count N] [--strict-count] IMAGE [IMAGE ...]
```

- `IMAGE` is one or more positional filenames. Their argument order is retained;
  shell globbing is the caller's responsibility. Do not recurse, list, or sort a
  directory.
- `--inventory PATH` is required.
- `--output PATH` defaults to `-` (stdout); `-` explicitly means stdout.
- `--expected-qr-count N` is an optional per-image hint (`N >= 0`). With the
  default behavior, a mismatch only warns on stderr. `--strict-count` turns any
  mismatch into a nonzero exit status, while still emitting the recovered CSV.
  Without this option, no count warning is produced.
- `--version` and `--help` are standard argparse options.

Stdout is reserved for CSV data. All diagnostics go to stderr and have stable,
short wording. Diagnostics identify the image and detection ordinal, or a short
non-reversible payload fingerprint; they do not print commissioning payloads or
pairing codes by default.

## Input inventory

Read the inventory as UTF-8 CSV (accept an optional UTF-8 BOM). The header must
contain exactly `code,descriptor` in that order. Every subsequent record must
have exactly two fields; blank records are ignored only when they are genuinely
empty CSV lines. `descriptor` is preserved as supplied, including an empty value
and meaningful whitespace. CSV quoting, commas, and newlines in descriptors are
handled by the CSV library.

Normalize each `code` before validation:

1. remove surrounding whitespace and all spaces/hyphens used as visual
   separators;
2. require exactly 11 or 21 ASCII decimal digits;
3. verify the final digit with the Verhoeff-10 checksum; and
4. retain leading zeroes.

The normalized inventory key is the value used for matching. A malformed row,
invalid encoding, wrong header/field count, empty code, or duplicate normalized
code is a fatal inventory error. Duplicate codes are rejected even if their
descriptors are equal. Validate the complete inventory before opening/producing
output or decoding images.

## Scan and payload pipeline

Each image is loaded once and passed to a multi-barcode decoder. The decoder
adapter is configured for QR codes and uses `zxingcpp.read_barcodes()`; it does
not invoke a second directory scan or shell command.

For each decoder result:

1. Normalize only surrounding decoded-text whitespace. Do not case-fold or
   otherwise rewrite the payload.
2. Keep a QR result only when its decoded text begins with the exact, case-
   sensitive `MT:` prefix. A QR with another payload is a non-Matter QR: warn
   once for that result and exclude it.
3. Parse the complete Matter setup payload. An `MT:` value with invalid Base38,
   an invalid length/chunk, invalid fixed fields, invalid optional data, or an
   unsupported version is a malformed Matter QR: warn once and exclude it.
4. For a valid result, derive one manual pairing code for each logical
   `SetupPayload` returned by the official-compatible parser. Normal single
   payloads produce one row. Official concatenated payload chunks (separated as
   Project CHIP specifies) are parsed in chunk order and produce one row per
   logical payload; a parse failure makes the QR result malformed rather than
   partially accepting it.
5. Match the canonical derived digits against the normalized inventory key using
   exact string equality. A match supplies `descriptor` and `pairing_code`; a
   valid unmatched result supplies blank `descriptor` and blank `pairing_code`.

A valid QR result is counted once for `--expected-qr-count`, even if an official
concatenated QR yields multiple logical payload rows. Duplicate decoder results
are still valid detections and count separately.

### Matter parser rules

Implement `matter.py` as an isolated parser with test vectors. Its behavior must
mirror the Project CHIP `SetupPayload` parser rather than treating any string
starting with `MT:` as valid:

- Decode the Base38 section with the Project CHIP alphabet
  `0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.`.
- Follow Project CHIP's little-endian chunk convention: each three input bytes
  is represented as five Base38 characters, with the least-significant digit
  first; a final two-byte or one-byte chunk uses four or two characters.
- Read the mandatory 88-bit packed payload in least-significant-bit-first field
  order: version (3 bits), vendor ID (16), product ID (16), commissioning flow
  (2), rendezvous information (8), discriminator (12), setup PIN (27), and
  zero padding (4). Validate reserved/padding bits and field ranges using the
  same rules as the official parser.
- Validate any remaining optional QR information using the Matter TLV rules and
  support official `*`-separated/concatenated payload representation. Structure
  children must be tagged, array children anonymous, lists follow CHIP's
  unrestricted tag rule, and every container terminator must be anonymous.
  Optional values are not needed for matching, but malformed optional data must
  not be silently accepted.
- Require a supported current payload version and a valid commissioning flow;
  reject values the Project CHIP parser rejects. Treat the QR text as opaque
  output data after surrounding whitespace normalization.

Derive the manual code exactly as Project CHIP's
`ManualSetupPayloadGenerator` does:

```text
short_discriminator = (discriminator >> 8) & 0xf
chunk1 = ((short_discriminator >> 2) & 0x3)
          | ((commissioning_flow != STANDARD) << 2)
chunk2 = (setup_pin & 0x3fff) | ((short_discriminator & 0x3) << 14)
chunk3 = (setup_pin >> 14) & 0x1fff
base = zero_pad(chunk1, 1) + zero_pad(chunk2, 5) + zero_pad(chunk3, 4)
if commissioning_flow != STANDARD:
    base += zero_pad(vendor_id, 5) + zero_pad(product_id, 5)
pairing_code = base + verhoeff_check_digit(base)
```

The result is therefore always canonical digits of length 11 (standard flow) or
21 (non-standard flow). Implement the Verhoeff-10 check digit with the official
algorithm/table, and test leading-zero and boundary values. Do not use integer
conversion for matching, because it would lose leading zeroes.

### Decoder ordering and duplicates

ZXing-C++ does not define an application-level ordering contract, so normalize
its result positions to an axis-aligned bounding box and sort each image by:

```text
(min_y, min_x, max_y, max_x, normalized_text)
```

This is deterministic top-to-bottom, then left-to-right ordering, with decoded
text as a tie-breaker. The original image argument order is the outer ordering.
Logical payload chunks remain in parser order. A result with the same normalized
QR text as an earlier result anywhere in the run is a duplicate: emit it anyway
and warn for every occurrence after the first. This covers duplicate detections
within one image and the same QR appearing in multiple images. There is no
silent deduplication, so repeated rows are intentional and rerunning the same
inputs produces the same CSV.

## Output CSV

Write exactly this header and no additional columns:

```csv
qr_code,descriptor,pairing_code
```

For each valid logical payload, preserve the normalized decoded QR text in
`qr_code`. For a matched payload, write the inventory descriptor and canonical
11- or 21-digit pairing code. For an unmatched payload, write empty descriptor
and pairing-code fields. Every valid detection, including duplicates, produces
its row.

Use Python's CSV writer with UTF-8 and `newline=""`; quote fields as required.
When `--output -` is selected, write only CSV to stdout. For a file output, write
to a same-directory temporary file and atomically replace the destination after
serialization. This also means a strict count failure still leaves a complete,
recovered CSV. An output open/write/replace failure is reported on stderr and
returns an output-error status.

## Image failures and diagnostics

An image that cannot be opened, decoded, or safely represented is warned about
and skipped; other images continue and their rows are emitted. A readable image
with no QR results is not itself an error (but can trigger the expected-count
warning). Every decoded QR that is non-Matter or malformed receives its own
warning and contributes no output row. Every valid but unmatched QR receives its
own warning and does contribute a blank-valued row. Duplicate warnings are
separate from unmatched warnings when both apply.

The count hint compares `valid Matter QR results` (including duplicate results,
but excluding malformed/non-Matter QR results) with `N` independently for each
input image. Count warnings name the image and observed/expected numbers. A
strict mismatch changes the final status after CSV writing; it does not discard
recovered rows.

Suggested exit statuses:

| Status | Meaning |
|---:|---|
| 0 | CSV emitted; no fatal image/output issue and no strict mismatch |
| 2 | CLI usage error or fatal malformed/unreadable inventory |
| 3 | Output cannot be created/written/replaced |
| 4 | One or more image inputs could not be read/decoded (recovered rows may exist) |
| 5 | `--strict-count` mismatch (CSV was emitted) |

Warnings for non-Matter QR codes, malformed QR codes, unmatched valid codes,
duplicates, and non-strict count mismatches do not change the status. If several
recoverable statuses occur, use the highest-priority status in the order
`output (3)`, image failure (4), strict mismatch (5), while inventory/usage
errors fail before scanning; document and test the precedence explicitly. A
future implementation may use a named status enum internally, but the shell
contract must remain stable.

## Architecture

Proposed source layout:

```text
pyproject.toml
src/mattermatch/
  __init__.py
  __main__.py          # python -m mattermatch
  cli.py               # argparse, orchestration, exit mapping
  inventory.py         # CSV loading, code normalization, duplicate checks
  decoder.py           # BarcodeDecoder protocol and ZXing-C++ adapter
  matter.py             # Base38, packed fields, TLV, Verhoeff, parser model
  matching.py           # ordered detections and inventory joins
  diagnostics.py        # stderr warning/error formatting and fingerprints
  output.py             # exact CSV schema and atomic file writer
tests/
  test_inventory.py
  test_matter.py
  test_matching.py
  test_cli.py
  fixtures/             # small licensed QR/image and official payload vectors
```

Keep `matching.py` dependent on a decoder protocol, not on ZXing. Unit tests can
then supply deterministic fake detections with positions, while a small number
of integration tests exercise the real native decoder. Keep parser errors typed
(`NonMatter`, `MalformedMatter`, `UnsupportedPayload`) so the CLI can warn
without leaking exception details.

The orchestration sequence is:

1. parse arguments;
2. load and validate inventory;
3. initialize decoder;
4. process images in argument order, sort detections geometrically, parse and
   match while collecting diagnostics and per-image counts;
5. serialize the exact output CSV;
6. return the mapped status, including strict count mismatch only after output.

No global mutable state or parallel image processing is used in v1; avoiding
parallelism makes ordering, diagnostics, and native decoder resource behavior
predictable.

## Packaging and dependencies

Use `pyproject.toml` with a `setuptools` entry point:

```toml
[project.scripts]
mattermatch = "mattermatch.cli:main"
```

Target a currently supported CPython release (3.10+ is a reasonable floor).
Runtime dependencies should be limited to:

- `zxing-cpp` Python bindings for multi-barcode QR decoding;
- `Pillow` for header-first local image loading and checked pixel expansion;
- `numpy`, as the image-array interchange type used by the binding.

Use the standard library for argparse, CSV, temporary files, hashing, and
Verhoeff logic. Pin minimum tested versions, test the supported wheel/platform
matrix in CI, and provide a source-build note because zxing-cpp contains a C++
extension. Do not add the full Project CHIP SDK as a runtime dependency.

## Security and privacy

Processing is local and performs no network requests. Treat both images and CSV
as untrusted input: bound decoded image dimensions/pixel count and QR text/TLV
sizes, reject impossible Base38 lengths, avoid unbounded diagnostic strings, and
never interpolate filenames into shell commands. Do not print raw QR payloads,
pairing codes, inventory rows, or image bytes in diagnostics. The CSV itself is
sensitive commissioning material; document that stdout/file output must be
protected and create new output files with restrictive permissions where the
platform permits. Preserve descriptor text rather than silently applying
spreadsheet/formula transformations, and document that consumers must treat CSV
as untrusted data. Avoid retaining image buffers after each image is processed.

## Tests and acceptance criteria

Tests should be thorough at the pure-function and CLI-contract levels:

- Base38 encode/decode vectors and official Project CHIP payload examples,
  including malformed characters, chunk lengths, padding, version, flow, and
  optional/concatenated payload validation.
- Manual-code vectors for standard and non-standard flow, 11/21 digits,
  leading-zero PIN/Vendor/Product values, discriminator boundaries, and Verhoeff
  check digits; compare with official Project CHIP output.
- Inventory normalization of spaces/hyphens, preserved leading zeroes, valid and
  invalid lengths, quoted descriptors, malformed CSV, and duplicate normalized
  codes.
- Fake-decoder tests for 12 detections, geometric ordering, duplicate emission
  and warnings, non-Matter/malformed warnings, unmatched blank rows, and input
  filename ordering.
- CLI tests proving stdout contains only the exact three-column CSV, stderr has
  diagnostics, file output is atomic, no-QR images produce a header, unreadable
  images recover other rows, and strict count mismatch still writes recovered
  CSV with status 5.
- A small real `zxing-cpp` integration fixture containing multiple QR codes,
  plus a platform-independent fake-decoder suite so CI is not wholly dependent
  on native image behavior.

Done means the implementation can be run repeatedly on unchanged inputs and
produce byte-for-byte identical CSV and deterministic diagnostics (apart from
OS-specific path rendering), while accepting only parser-validated Matter
payloads and preserving every valid detection.

## Official technical references

The implementation should pin or periodically review behavior against these
Project CHIP sources:

- [`SetupPayload.h`](https://github.com/project-chip/connectedhomeip/blob/master/src/setup_payload/SetupPayload.h)
  — field widths, QR prefix, payload size, and manual-code constants.
- [`QRCodeSetupPayloadParser.cpp`](https://github.com/project-chip/connectedhomeip/blob/master/src/setup_payload/QRCodeSetupPayloadParser.cpp)
  — payload extraction, Base38 parsing, optional TLV, and concatenation behavior.
- [`Base38Encode.cpp`](https://github.com/project-chip/connectedhomeip/blob/master/src/setup_payload/Base38Encode.cpp)
  and [`Base38Decode.cpp`](https://github.com/project-chip/connectedhomeip/blob/master/src/setup_payload/Base38Decode.cpp)
  — alphabet and little-endian chunk encoding.
- [`ManualSetupPayloadGenerator.cpp`](https://github.com/project-chip/connectedhomeip/blob/master/src/setup_payload/ManualSetupPayloadGenerator.cpp)
  — 11/21-digit layout and Verhoeff check digit.
- [`zxing-cpp` Python wrapper README](https://github.com/zxing-cpp/zxing-cpp/blob/master/wrappers/python/README.md)
  — installation and `read_barcodes()` multi-result API.

## Alternatives considered

- **Recommended: zxing-cpp Python binding plus a focused parser.** It provides a
  multi-result API, avoids a system `libzbar`, and keeps Matter semantics under
  test in this project.
- **pyzbar/ZBar.** Familiar but adds a platform-level ZBar dependency and gives
  less predictable packaging across platforms; it does not remove the need for
  our Matter parser.
- **Invoke `chip-tool` or the full Project CHIP SDK.** This would maximize reuse
  of the reference parser but is a heavyweight, non-portable runtime dependency
  and makes a small offline CSV tool difficult to install. Use official source
  behavior and vectors without coupling the CLI to that SDK.

## Premortem and mitigations

| Risk/assumption | Mitigation |
|---|---|
| Native zxing wheels differ by OS or image format | Pillow header preflight, decoder adapter, CI wheel matrix, and fake-decoder tests |
| A parser accepts a prefix but mishandles Base38 bit order or Verhoeff | Isolate `matter.py`, use official source links and golden vectors before integration |
| Duplicate native detections make results look inflated | Preserve every detection as required, warn deterministically, and define count semantics |
| Pairing codes lose leading zeroes or separators mismatch inventory | Normalize only inventory visual separators, validate digits/length, and compare strings |
| A strict failure causes users to lose recovered data | Serialize/atomically replace output before returning status 5 |
| Diagnostics leak commissioning secrets | Use image/ordinal and short fingerprints, cap text sizes, and keep raw values only in CSV |
