import io

import pytest

from mattermatch.cli import EXIT_IMAGE, EXIT_OK, EXIT_OUTPUT, EXIT_STRICT_COUNT, main
from mattermatch.decoder import Detection

VALID = "MT:M5L90MP500K64J00000"


class Fake:
    def __init__(self, values):
        self.values = values

    def decode(self, path):
        value = self.values.get(path, [])
        if isinstance(value, BaseException):
            raise value
        return value


def inventory(tmp_path):
    path = tmp_path / "inventory.csv"
    path.write_text("code,descriptor\n00204800002,Kitchen\n", encoding="utf-8")
    return path


def test_stdout_is_exact_csv_and_diagnostics_are_stderr(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    rc = main(
        ["--inventory", str(inventory(tmp_path)), "image.png"],
        decoder=Fake({"image.png": [Detection(VALID), Detection("hello")]}),
        stdout=out,
        stderr=err,
    )
    assert rc == EXIT_OK
    assert out.getvalue() == "qr_code,descriptor,pairing_code\n" + f"{VALID},Kitchen,00204800002\n"
    assert out.getvalue().count("warning") == 0
    assert "non-Matter QR" in err.getvalue()


def test_no_qr_still_emits_header(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    assert main(["--inventory", str(inventory(tmp_path)), "empty.png"], decoder=Fake({"empty.png": []}), stdout=out, stderr=err) == EXIT_OK
    assert out.getvalue() == "qr_code,descriptor,pairing_code\n"
    assert err.getvalue() == ""


def test_recovered_rows_and_strict_count_status(tmp_path):
    destination = tmp_path / "result.csv"
    err = io.StringIO()
    rc = main(
        ["--inventory", str(inventory(tmp_path)), "--output", str(destination), "--expected-qr-count", "2", "--strict-count", "one.png"],
        decoder=Fake({"one.png": [Detection(VALID)]}),
        stderr=err,
    )
    assert rc == EXIT_STRICT_COUNT
    assert destination.read_text(encoding="utf-8").endswith("Kitchen,00204800002\n")
    assert "expected 2" in err.getvalue()


def test_unreadable_image_recovers_other_image(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    rc = main(
        ["--inventory", str(inventory(tmp_path)), "bad.png", "good.png"],
        decoder=Fake({"bad.png": OSError(), "good.png": [Detection(VALID)]}),
        stdout=out,
        stderr=err,
    )
    assert rc == EXIT_IMAGE
    assert VALID in out.getvalue()
    assert "bad.png" in err.getvalue()


def test_output_replacement_preserves_existing_file_on_serialization_failure(tmp_path):
    from mattermatch.output import OutputError, write_csv

    destination = tmp_path / "result.csv"
    destination.write_text("old content\n", encoding="utf-8")

    def rows():
        yield ("one", "two", "three")
        raise ValueError("late failure")

    with pytest.raises(OutputError):
        write_csv(rows(), destination)
    assert destination.read_text(encoding="utf-8") == "old content\n"


def test_image_failure_precedes_strict_count_status(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    rc = main(
        ["--inventory", str(inventory(tmp_path)), "--expected-qr-count", "1", "--strict-count", "bad.png"],
        decoder=Fake({"bad.png": OSError()}),
        stdout=out,
        stderr=err,
    )
    assert rc == EXIT_IMAGE
    assert "expected 1" in err.getvalue()


def test_output_failure_precedes_recoverable_statuses(tmp_path):
    destination = tmp_path / "existing-directory"
    destination.mkdir()
    rc = main(
        ["--inventory", str(inventory(tmp_path)), "--output", str(destination), "bad.png"],
        decoder=Fake({"bad.png": OSError()}),
        stderr=io.StringIO(),
    )
    assert rc == EXIT_OUTPUT
