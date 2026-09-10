# Development

Returning after a break? Start with [the handoff](docs/HANDOFF.md). The
[README](README.md) describes usage; [DESIGN.md](DESIGN.md) records the CLI and
protocol contracts. [The release checklist](docs/RELEASING.md) covers packaging.

## Set up and test

Use CPython 3.10+ and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked --extra test
uv run --locked --extra test pytest -ra
uv run --locked mattermatch --version
uv run --locked mattermatch --help
uv run --locked mattermatch inventory check --help
uv run --locked mattermatch inventory normalize --help
uv run --locked mattermatch verify-pair --help
```

Without uv, use an activated virtual environment and
`python -m pip install -e '.[test]'`, then `python -m pytest -ra`. This resolves
available dependency versions rather than reproducing `uv.lock`.

Useful narrower checks:

```sh
uv run --locked --extra test pytest -m 'not integration' -ra
uv run --locked --extra test pytest -m integration -ra
uv run --isolated --locked --python 3.12 --extra test pytest -ra
```

The native integration tests generate synthetic QR images using zxing-cpp.
They skip when its barcode-generation API is absent, including on the declared
zxing-cpp 2.2.0 dependency floor. A green run with skips is **not** evidence that
native scanning was exercised. Read the summary. No real-device photo corpus or
installed real `chip-tool` is required by the current suite.

To check declared runtime dependency floors separately from the lockfile:

```sh
uv run --isolated --python 3.10 --extra test \
  --with 'numpy==1.24.0' --with 'Pillow==10.0.0' \
  --with 'zxing-cpp==2.2.0' pytest -ra
```

These are compatibility-test versions, not recommendations to deploy old image
libraries. Use maintained dependency versions for actual scans.

## Code map

| Area | Files |
|---|---|
| CLI dispatch, arguments, exit priorities | `src/mattermatch/cli.py` |
| Base38, packed fields, TLV, manual codes, Verhoeff | `src/mattermatch/matter.py` |
| Image preflight, native decoding, retry merge | `src/mattermatch/decoder.py` |
| Ordered detections, inventory joins, per-image counts | `src/mattermatch/matching.py` |
| Inventory validation and explicit normalization | `src/mattermatch/inventory.py` |
| CSV/JSONL serialization and atomic replacement | `src/mattermatch/output.py` |
| Redacted warnings and opt-in image artifacts | `src/mattermatch/diagnostics.py`, `artifacts.py` |
| QR/manual verification and bounded child processes | `src/mattermatch/verification.py` |

Tests generally mirror module names. `test_tranche.py` covers JSONL, inventory
maintenance, unmatched output, and artifact collisions; `test_second_tranche.py`
covers verification, subprocess limits, retries, and visual artifacts. These
historical names are not separate product versions or a roadmap.
`test_scan_output_safety.py`, `test_retry_merge.py`, and
`test_verification_flows.py` cover the three v0.2.1 release-blocker fixes.

## Change safely

- Add a regression test before changing behavior. Preserve deterministic order,
  duplicate semantics, leading zeroes, exact CSV columns, and exit priorities.
- Use injected decoders for orchestration tests and synthetic images for native
  tests. Do not commit actual commissioning credentials, inventories, or photos.
- File serialization must remain atomic; validation must precede side effects.
  Scan destinations must not alias the inventory or any source image; preserve
  the alias-check regressions in `test_scan_output_safety.py`.
- Keep scan diagnostics redacted. Verification stdout includes a setup PIN;
  output files and image artifacts are sensitive too.
- Keep native decoding out of the protocol/parser layer. Prioritize the remaining
  documented P2 issues over broad rewrites.
- No CI, formatter, linter, or type-checker configuration is currently committed.
  Do not confuse local passing tests with an established cross-platform matrix.

## Local cleanup

The starting tracked tree was clean. `.venv/`, `.pytest_cache/`, `__pycache__/`,
`build/`, `dist/`, and `*.egg-info/` are ignored generated state, not source.
Preview it with `git status --short --ignored`; do not use a blanket
`git clean -fdx`, which also removes ignored environments and potentially private
working data. Remove only explicitly reviewed generated paths if desired.

Keep inventories, photos, CSV/JSONL output, and diagnostic images **outside the
checkout**. The existing ignore rules do not protect arbitrary scan data.
Build releases into a fresh directory rather than uploading everything in the
existing `dist/`, which contained both 0.1.0 and 0.2.0 artifacts at review time.
