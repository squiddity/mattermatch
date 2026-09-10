# Release checklist

## Current checkpoint

**v0.2.1** is the first GitHub release checkpoint, with the three P1 review
blockers fixed. The [handoff](HANDOFF.md) lists four remaining P2 limitations;
they are explicitly outside this release's scope. Passing tests and a valid
wheel do not imply those limitations are resolved.

Both `pyproject.toml` and `src/mattermatch/__init__.py` say **0.2.1**. GitHub had
no remote tags or releases before this checkpoint. Release assets are a fresh
wheel, source archive, and SHA-256 checksum file; older checkout `dist/` files
are not release provenance. No PyPI upload is part of this release.

The steps below are the procedure for future releases. Publication remains an
explicit maintainer decision, not something these documentation commands do.

## 1. Select and record the version

- Check remote tags, GitHub releases, and the intended package index before
  selecting a version. Never reuse an already-published version for new bytes.
- Select the next appropriate version after 0.2.1; do not reuse existing tags or
  already-published artifact versions.
- Update `pyproject.toml`, `src/mattermatch/__init__.py`, and `uv.lock` together
  (`uv lock` after changing project metadata).
- Add a dated entry to [CHANGELOG.md](../CHANGELOG.md) and prepare release notes.
  State compatibility, known limitations, and whether it is a prerelease.
- Commit reviewed changes and confirm `git status --short` is empty before
  producing release artifacts.

## 2. Validate the source

```sh
uv sync --locked --extra test
uv run --locked --extra test pytest -ra
uv run --isolated --locked --python 3.12 --extra test pytest -ra
```

Also run the dependency-floor check in [CONTRIBUTING.md](../CONTRIBUTING.md).
Require real native integration execution on the primary supported environment;
review all skips. Test macOS/Windows before claiming support there. The current
review validated Linux only and did not run an installed real `chip-tool`.

Before a compatibility claim, add fixed synthetic image fixtures that can run
against zxing-cpp 2.2.0 without relying on its newer QR-generation API, and pin
the CHIP revision used for protocol vectors.

## 3. Build fresh and inspect

The following is a POSIX shell recipe, run from the repository root. Keep its
variables in the same shell. It intentionally avoids the old checkout `dist/`.

```sh
release_dir=$(mktemp -d "${TMPDIR:-/tmp}/mattermatch-release.XXXXXX")
uv build --out-dir "$release_dir/dist"
uvx twine check "$release_dir"/dist/*
uv venv --python 3.12 "$release_dir/venv"
uv pip install --python "$release_dir/venv/bin/python" "$release_dir"/dist/*.whl
(
  cd "$release_dir"
  ./venv/bin/mattermatch --version
  ./venv/bin/python -m mattermatch --help
  ./venv/bin/python -c 'from importlib.metadata import version; import mattermatch; assert version("mattermatch") == mattermatch.__version__'
)
printf 'Review artifacts in: %s\n' "$release_dir/dist"
```

Inspect wheel/sdist contents for accidental private files. `MANIFEST.in` includes
maintainer/design docs and `uv.lock` in the source archive; confirm these remain
present so an unpacked source archive is a useful offline handoff.

Smoke-test an actual synthetic QR scan from the installed wheel, not merely an
editable checkout. Reinstall the sdist in a second clean environment and test it
too. Review metadata, license, entry points, and version consistency. `twine
check` validates distribution metadata, not application correctness or safety.

## 4. Publish only by explicit decision

Once all gates pass, choose the distribution channel and approve publication:

1. Create an annotated `vVERSION` tag on the exact tested commit.
2. Push that tag deliberately and create a matching GitHub release with notes.
3. Attach only the freshly validated wheel/sdist for that version; optionally
   publish the same files to PyPI if wanted and credentials are configured.
4. Verify an installation from the published tag/artifact in a clean environment.
5. Record tag, commit, date, artifact checksums, and tested platforms here or in
   release notes, then update the handoff and README install instructions.

Do not use `dist/*` from the working checkout for an upload: it may contain
stale artifacts for multiple versions. Tag/release/upload steps are deliberately
not automated by these docs.
