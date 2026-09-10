# Changelog

## 0.2.1 — 2026-09-10

First GitHub release checkpoint. Earlier entries are retrospective, untagged
source snapshots, not claims of earlier publication.

### Fixed

- Reject scan output that aliases the inventory or any input image, including
  symlinks and existing hard links, before decoding or artifact side effects.
- Preserve separate rotated, same-payload QR symbols during retry merging by
  requiring substantial overlap and center/size agreement.
- Correct optional `chip-tool` cross-checks for user-action commissioning: QR
  flow 1 corresponds to manual-code flow 2, not 1.

### Maintenance

- Add dedicated regressions, including independently specified CHIP v1.4.0.0
  flow vectors and realistic synthetic tool output.
- Add maintainer setup, code map, pause/resume handoff, and release checklist.
- Include maintainer/design docs and `uv.lock` in the source distribution.
- Document sensitive channels and remaining P2 limitations in
  [the handoff](docs/HANDOFF.md).

Validation: 149 tests passed on Linux with Python 3.10 and 3.12. Declared runtime
minimums: 147 passed, two native integration tests skipped. No live `chip-tool`
or macOS/Windows support claim is made.

## 0.2.0 — untagged snapshot

Source: `1275efd` (2026-08-18), “Add MatterMatch validation and recovery tools”.

- Add inventory check and explicit normalization with checksum-gated,
  opt-in leading-zero repair.
- Add source-aware JSONL output; valid unmatched results retain pairing codes.
- Add semantic QR/manual-code verification and optional bounded `chip-tool`
  cross-checking.
- Add opt-in preprocessing retries and deterministic visual diagnostic artifacts.
- Expand parser, recovery, artifact, and subprocess-boundary tests.

Superseded by the v0.2.1 release checkpoint. See [the handoff](docs/HANDOFF.md)
for fixes and remaining limitations.

## 0.1.0 — untagged snapshot

Source: `9137db0` (2026-08-17), “Build Matter QR matching CLI”.

- Introduce local-only, explicit-image Matter QR scanning and inventory matching.
- Add Base38/TLV parsing, manual-code generation, and Verhoeff validation.
- Add deterministic CSV output, diagnostics, and strict per-image count handling.
- Package the `mattermatch` command with native zxing-cpp image decoding.
