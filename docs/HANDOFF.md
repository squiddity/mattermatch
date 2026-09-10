# Pause / resume handoff

## Start here

This handoff accompanies **v0.2.1** (2026-09-10), the first GitHub release
checkpoint. The three P1 issues identified in the review of `1275efd` (0.2.0)
are fixed with regression tests. The four P2 issues below remain open by the
chosen release scope; do not mistake this checkpoint for exhaustive protocol,
resource-bound, or cross-platform validation.

Release: <https://github.com/squiddity/mattermatch/releases/tag/v0.2.1>

When returning:

1. Read this page and `git log -5 --oneline`; check `git status --short`.
2. Follow [CONTRIBUTING.md](../CONTRIBUTING.md) to restore the locked environment
   and run tests. Do not assume an old `.venv/` or `dist/` is current.
3. Add CI and address the remaining P2 findings with regression tests.
4. Use [RELEASING.md](RELEASING.md) for subsequent releases.

A broad refactor is not the highest-value next step. Directory/archive scanning,
reconciliation/coverage merging, per-image count maps, and a UI remain deferred.

## What exists

- Local-only scanning of explicitly named images, strict inventory validation,
  Matter payload parsing/manual-code derivation, deterministic CSV/JSONL joins.
- Inventory check/normalization, bounded photometric retries, sensitive visual
  artifacts, semantic pair verification, and an optional external tool check.
- A modular implementation under `src/mattermatch/`, 149 tests, an MIT license,
  packaging metadata, lockfile, and detailed [design](../DESIGN.md).
- Maintainer docs and the lockfile are included in the source distribution via
  `MANIFEST.in`. No committed CI or release automation exists yet.
- Old ignored `dist/` contains 0.1.0 and 0.2.0 builds. These were left untouched;
  v0.2.1 release assets are built fresh, not selected from that directory.

## Fixed in v0.2.1

### Scan output cannot alias an input

`src/mattermatch/cli.py` rejects scan output that aliases the inventory or any
source image before decoder initialization or artifact creation. Direct,
relative/resolved, symlink, and existing hard-link aliases return status 2.
Unverifiable paths fail closed with status 3. Distinct output files retain atomic
replacement and missing images remain recoverable.

Regression coverage: `tests/test_scan_output_safety.py` covers both CSV/JSONL,
input types, aliases, unchanged bytes, side effects, and separate destinations.
The check is not a defense against hostile concurrent directory mutation.
Shell redirection can truncate an input before the CLI starts; never redirect
stdout onto an input.

### Retry merging preserves separate rotated QRs

`src/mattermatch/decoder.py` requires at least half of each box to overlap plus
center/size agreement before merging equal-text detections across retries.
Incidental overlap no longer discards a separate rotated symbol. Existing
same-variant duplicates and detections without usable geometry are preserved.

Regression coverage: `tests/test_retry_merge.py` uses the native reproduction's
boxes `(59,59,206,208)` / `(189,189,336,338)` and covers jitter, distinct centers,
size differences, tiny/degenerate boxes, and duplicate semantics. The merge
remains a geometry heuristic, not proof of physical symbol identity.

### Chip-tool flow checks reflect the manual representation

`src/mattermatch/verification.py` checks the QR flow as 0/1/2, while manual codes
are checked as standard 0 or nonstandard Custom 2. A UserActionRequired QR (1)
therefore no longer incorrectly fails when its manual code parses as Custom.

Regression coverage: `tests/test_verification_flows.py` contains independent
literal vectors and synthetic tool logs referenced to CHIP `v1.4.0.0`, including
all flows, wrong-flow rejection, optional flow fields, and unchanged local
verification. No installed live `chip-tool` was used.

## Validation evidence

All results are local Linux checks, not a claim about every supported platform.

| Check | Result |
|---|---|
| Locked suite, CPython 3.10.20 | 149 passed; no skips |
| Isolated locked suite, CPython 3.12.13 | 149 passed; no skips |
| Python 3.10, NumPy 1.24.0 / Pillow 10.0.0 / zxing-cpp 2.2.0 | 147 passed, 2 native integration tests skipped |
| Independent review of the three fixes and regressions | No release-blocking findings |
| Fresh wheel/sdist build and `twine check` | Passed; docs/lockfile present in sdist |
| Full suite against clean wheel and sdist installations, Python 3.12 | 149 passed in each environment |
| Installed-wheel native CLI smoke | CSV, JSONL, retries, artifacts, collision protection, flow-1 verification passed |

Distribution validation is also recorded in the GitHub release notes. The
Python 3.10 locked environment used NumPy 2.2.6, Pillow 12.3.0, and zxing-cpp
3.1.1. Native tests exercise generated QR images, not a real-device photo corpus.
No physical-device commissioning, live `chip-tool`, macOS, or Windows validation
was performed. Package metadata checks are not security audits.

## Remaining P2 findings

Locations name the affected function, since line numbers changed after review.
Reproductions used temporary/synthetic data, not real commissioning material.

### Detect native result saturation

**`src/mattermatch/decoder.py`, `ZXingCppDecoder._decode_rgb`**

A generated 272-QR grid (1870×1760 pixels) yielded exactly 255 rows with both
normal and retry scans on zxing-cpp 3.1.1, without a truncation warning or image
failure. The Python-side 512 retry limit does not raise the binding's effective
native limit. It is not an end-to-end completeness bound.

Recover saturation with a supported approach, or report a clear
potentially-incomplete result and non-success status. Test binding versions and
document the real limit. A strict expected-count hint detects mismatches when
the true count is known, but is not a general fix.

### Preserve raw-text bounds at the scan boundary

**`src/mattermatch/matching.py`, `process_images`**

Scanning passes already-stripped text to the parser. A valid QR followed by
4,100 spaces is rejected by direct parsing yet accepted by scanning and retained
in JSONL. This reproduction used an injected detection.

Pass `raw_text` to `parse_matter_payload`, retaining normalized text separately
for matching/CSV. Add scan-level whitespace-padding regressions for both formats.

### Accept range-valid wider TLV integer encodings

**`src/mattermatch/matter.py`, `_validate_optional`**

Optional-data parsing rejects 64-bit integer wire encodings even when values fit
the required int32/uint32 range. CHIP's typed TLV getters accept such range-valid
encodings. Synthetic serial-number-1 uint64 payload currently rejected:
`MT:M5L90MP500K64J0A33P0PF7100000000000Q940`.

Validate signedness, tag, and value range rather than rejecting wider encoding
widths outright. Add pinned-reference serial/vendor vectors including overflow
rejections. This finding is source-based, not a live interoperability test.

### Bound artifact collision comparisons

**`src/mattermatch/artifacts.py`, `_install_bytes`**

`destination.read_bytes()` loads an entire existing file to check for identical
bytes. Comparing a one-byte candidate with a 64 MiB sparse collision caused
roughly 64 MiB of extra peak allocation. Larger collisions can exhaust memory.

Reject mismatched sizes first and compare incrementally with bounded reads,
retaining no-replace installation and handling file changes safely. Test a large
sparse collision without reading its complete content into memory. Use a private,
trusted diagnostics directory meanwhile.

## Maintenance follow-ups

1. **Add CI:** locked tests on the minimum Python plus a newer version; build and
   clean-install checks. Add OS coverage only as exercised support, not a badge.
2. **Close native-test skips:** commit small synthetic, clearly licensed QR
   images so minimum decoder versions do not need their own generation API.
3. **Extend protocol evidence:** new flow tests pin CHIP `v1.4.0.0`; pin remaining
   source references and add independent optional-TLV/golden vectors. The design
   still links upstream `master`.
4. **Low-risk housekeeping:** rename tranche tests by feature; remove genuinely
   unused constants/compatibility branches after checking callers; improve
   `inventory --help` dispatch (currently it shows scan help).
5. **Packaging polish:** add project URLs; consolidate duplicate version
   declarations or enforce their equality in CI.

## Protect the working data

CSV/JSONL contain commissioning credentials; `verify-pair` stdout includes the
setup PIN; visual artifacts contain image material. CLI values can remain in
shell history and process listings. Keep real data outside the checkout and use
a private diagnostics directory. Redacted scan warnings do not make the other
channels safe to share.

Existing generated ignored state was left in place deliberately. Follow targeted
cleanup guidance in CONTRIBUTING rather than a blanket removal command, and
rebuild release artifacts from source.
