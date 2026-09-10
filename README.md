# MatterMatch

MatterMatch scans explicitly supplied image filenames, decodes Matter commissioning QR codes, derives their manual pairing codes, and joins them to an inventory CSV. It is local-only; shell glob expansion is the caller's responsibility and directories are never searched.

## Project status

The current source version is **0.2.1**. Before publishing or resuming work, read
[the handoff and known issues](docs/HANDOFF.md). Development setup is in
[CONTRIBUTING.md](CONTRIBUTING.md); see also the
[release checklist](docs/RELEASING.md), [changelog](CHANGELOG.md), and
[design contract](DESIGN.md). The three release-blocking issues found in the
initial review are fixed; remaining limitations and local validation are recorded
in the handoff. GitHub releases are available on the
[releases page](https://github.com/squiddity/mattermatch/releases).

## Install

A current CPython (3.10+) and a platform with wheels for the native decoder are recommended. For an isolated end-user installation directly from GitHub, use pipx:

```sh
pipx install git+https://github.com/squiddity/mattermatch.git
```

Upgrade that installation after a new release with:

```sh
pipx upgrade mattermatch
```

For development from a local checkout (including tests):

```sh
uv sync --locked --extra test
uv run --locked --extra test pytest -ra
```

Without uv, use an activated virtual environment and
`python -m pip install -e '.[test]'`. The GitHub pipx install above follows the
default branch, not an immutable release tag.

pipx automatically installs `zxing-cpp`, Pillow, and NumPy into MatterMatch's isolated environment. Pillow headers are checked before pixel data is loaded, so oversized compressed images are rejected safely. If a wheel is unavailable, `zxing-cpp` requires a C++20-capable source-build environment.

## Usage

The inventory header must be exactly `code,descriptor`; codes are 11- or 21-digit Verhoeff-validated Matter manual codes. Spaces and hyphens may be used as visual separators.

```sh
mattermatch --inventory inventory.csv --output matches.csv images/*.png
mattermatch --inventory inventory.csv --format jsonl image-a.png image-b.jpg
mattermatch --inventory inventory.csv --expected-qr-count 2 --strict-count image-a.png image-b.jpg
# Opt-in recovery and sensitive visual artifacts:
mattermatch --inventory inventory.csv --retry-preprocessing \
  --diagnostics-dir scan-artifacts image-a.png
```

The legacy scan grammar also permits an image literally named `inventory` or
`verify-pair`; those words are treated as subcommands only when the following
arguments make that command shape unambiguous.

**Use an output path different from the inventory and every input image.**
Scan mode rejects direct, resolved-path, symlink, and existing hard-link aliases
with status 2 before decoding or writing artifacts. Paths that cannot be safely
compared return status 3. Do not redirect stdout onto an input file: the shell
can truncate it before MatterMatch runs.

Without `--output`, the exact historical `qr_code,descriptor,pairing_code` CSV
is written to stdout (`--format csv` is the default). A valid unmatched QR has
an empty descriptor but retains its derived pairing code. `--format jsonl`
emits source-aware objects with source image, detection ordinal, bounding box,
QR/pairing values, descriptor, and matched/unmatched status. Warnings and
errors go to stderr. A strict count mismatch still writes recovered output.
Exit statuses are 0 (success), 2 (usage/inventory), 3 (output), 4 (image
recovery), 5 (strict count mismatch), and 6 (diagnostic artifact failure);
output errors take precedence over artifact/image/count statuses. Recovery is
bounded and deterministic: the original image is tried first, followed by
fixed grayscale/autocontrast, contrast, and sharpness variants. Detections
from retries are merged only when text and geometry identify the same physical
QR; identical payloads at distinct positions remain separate. `--expected-qr-count` is compared only with validated Matter payload detections;
retry preprocessing always completes its bounded variant set, and each variant
retains at most 512 detections at the Python layer. The tested native binding
can saturate at 255 results without a warning, so this is not a completeness
guarantee; see the handoff. Retry merging requires substantial overlap and
center/size agreement, preserving separate rotated symbols with only incidental
bounding-box overlap. Severe glare or occlusion still requires a better image.

Visual artifacts are never produced by default. When `--diagnostics-dir` is
provided, each input gets a safe, deterministic annotated PNG and contact-sheet
PNG named by input ordinal and a short path hash (not by the QR payload).
Existing unrelated files are not overwritten; missing geometry produces a
warning and no crops. Artifacts cap annotated detections/crops and clamp crop
sizes, while artifact write failures return status 6. Artifacts
contain sensitive image data and should be protected.

Semantic verification is separate from inventory scanning:

```sh
mattermatch verify-pair --qr MT:... --pairing-code 00204800002
mattermatch verify-pair --json --qr MT:... --pairing-code 002-048-000-02
mattermatch verify-pair --chip-tool /path/to/chip-tool --qr MT:... --pairing-code CODE
```

`verify-pair` fully parses the QR, normalizes and Verhoeff-validates an 11- or
21-digit code without losing leading zeroes, and reports setup PIN,
discriminator/short-discriminator, commissioning flow, expected code length,
and PASS/FAIL. Status 0 means PASS; status 7 means semantic mismatch,
malformed input, a failed optional chip-tool cross-check, or an ambiguous
concatenated (`*`) QR. Status 2 remains CLI usage failure. Concatenated QR
chunks are fully validated but deliberately rejected by this one-code command;
normal scanning retains one output row per logical chunk. `--chip-tool` invokes
only an already-installed executable, separately parsing both the QR payload and
normalized manual pairing code with argument arrays, bounded live output, and a
timeout; on POSIX it isolates and terminates the complete subprocess group to
prevent inherited-pipe descendants from surviving. MatterMatch never downloads
or builds it; platforms without group termination use a bounded direct-process
fallback.

Inventory maintenance is explicit and never part of normal scanning:

```sh
mattermatch inventory check inventory.csv
mattermatch inventory normalize INPUT.csv --columns descriptor,code --no-header \
  --pad-leading-zeroes --output inventory.csv
```

`--columns` must be `code,descriptor` or `descriptor,code`; omit `--no-header`
when the input has that declared header. Normalization always writes a new
canonical `code,descriptor` file atomically. Leading-zero repair is opt-in and
checksum-gated: exactly one valid 11- or 21-digit candidate is required;
none, ambiguity, and duplicates fail, and the input is never overwritten.

Scan diagnostics do not print QR payloads or pairing codes, but CSV/JSONL and
visual artifacts contain sensitive commissioning data. `verify-pair` stdout
also includes the setup PIN, and command-line credentials can appear in shell
history and process listings. Protect these channels, keep real inputs/outputs
outside the checkout, and treat descriptor/CSV content as untrusted data.
MatterMatch itself performs no network requests. Image and parser size checks
are implemented, with known boundary gaps recorded in the handoff.
