"""Scan output validation must precede decoding and artifact side effects."""
import io
import os
from pathlib import Path

import pytest

from mattermatch.cli import EXIT_IMAGE, EXIT_OK, EXIT_OUTPUT, EXIT_USAGE_OR_INVENTORY, main
from mattermatch.decoder import Detection

VALID = "MT:M5L90MP500K64J00000"


def _link(source, target, *, hard=False, directory=False):
    try:
        if hard:
            os.link(source, target)
        else:
            target.symlink_to(source, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"filesystem does not support requested link: {exc}")


@pytest.mark.parametrize("output_format", ["csv", "jsonl"])
@pytest.mark.parametrize("input_kind", ["inventory", "image"])
@pytest.mark.parametrize("alias", ["exact", "relative", "output-symlink", "input-symlink", "parent-symlink", "hardlink"])
def test_scan_rejects_input_alias_before_any_side_effect(
    tmp_path, monkeypatch, output_format, input_kind, alias
):
    inventory = tmp_path / "inventory.csv"
    inventory.write_bytes(b"code,descriptor\n00204800002,Kitchen\n")
    image = tmp_path / "image.png"
    image.write_bytes(b"source image must remain unchanged")
    source = inventory if input_kind == "inventory" else image
    original_inventory, original_image = inventory.read_bytes(), image.read_bytes()
    destination = source
    input_path = source
    if alias == "relative":
        monkeypatch.chdir(tmp_path)
        destination = Path(source.name)
    elif alias in {"output-symlink", "hardlink"}:
        destination = tmp_path / "alias"
        _link(source, destination, hard=alias == "hardlink")
    elif alias == "input-symlink":
        input_path = tmp_path / "input-alias"
        _link(source, input_path)
    elif alias == "parent-symlink":
        parent = tmp_path / "directory-alias"
        _link(tmp_path, parent, directory=True)
        destination = parent / source.name

    calls = []

    def decoder_factory():
        calls.append("initialized")
        raise AssertionError("collision must fail before decoder initialization")

    monkeypatch.setattr("mattermatch.cli.ZXingCppDecoder", decoder_factory)
    artifacts = tmp_path / "artifacts"
    out, err = io.StringIO(), io.StringIO()
    rc = main(
        [
            "--inventory", str(input_path if input_kind == "inventory" else inventory),
            "--output", str(destination), "--format", output_format,
            "--diagnostics-dir", str(artifacts),
            "missing-first-image.png",
            str(input_path if input_kind == "image" else image),
        ],
        stdout=out, stderr=err,
    )
    assert rc == EXIT_USAGE_OR_INVENTORY
    assert calls == []
    assert out.getvalue() == ""
    assert "scan output must be different" in err.getvalue()
    assert not artifacts.exists()
    assert inventory.read_bytes() == original_inventory
    assert image.read_bytes() == original_image
    assert destination.read_bytes() == source.read_bytes()
    if alias == "output-symlink":
        assert destination.is_symlink()


@pytest.mark.parametrize("output_format", ["csv", "jsonl"])
@pytest.mark.parametrize("existing", [False, True])
def test_distinct_scan_destination_is_still_written(tmp_path, output_format, existing):
    inventory = tmp_path / "inventory.csv"
    original = b"code,descriptor\n00204800002,Kitchen\n"
    inventory.write_bytes(original)
    destination = tmp_path / "result"
    if existing:
        destination.write_text("old output")

    class Decoder:
        def decode(self, path):
            return [Detection(VALID)]

    rc = main(
        ["--inventory", str(inventory), "--output", str(destination),
         "--format", output_format, "image.png"],
        decoder=Decoder(), stdout=io.StringIO(), stderr=io.StringIO(),
    )
    assert rc == EXIT_OK
    assert VALID in destination.read_text()
    assert inventory.read_bytes() == original


def test_missing_image_still_recovers_to_distinct_output(tmp_path):
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("code,descriptor\n00204800002,Kitchen\n")
    destination = tmp_path / "result.csv"

    class Decoder:
        def decode(self, path):
            if path == "missing.png":
                raise FileNotFoundError(path)
            return [Detection(VALID)]

    rc = main(
        ["--inventory", str(inventory), "--output", str(destination),
         "missing.png", "good.png"],
        decoder=Decoder(), stdout=io.StringIO(), stderr=io.StringIO(),
    )
    assert rc == EXIT_IMAGE
    assert VALID in destination.read_text()


def test_unverifiable_output_fails_closed(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("code,descriptor\n00204800002,Kitchen\n")
    destination = tmp_path / "result.csv"
    destination.write_bytes(b"previous output")
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == destination:
            raise OSError("sensitive OS details must not leak")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    out, err = io.StringIO(), io.StringIO()
    rc = main(
        ["--inventory", str(inventory), "--output", str(destination), "image.png"],
        stdout=out, stderr=err,
    )
    assert rc == EXIT_OUTPUT
    assert out.getvalue() == ""
    assert "could not be compared safely" in err.getvalue()
    assert "sensitive OS details" not in err.getvalue()
    assert destination.read_bytes() == b"previous output"
