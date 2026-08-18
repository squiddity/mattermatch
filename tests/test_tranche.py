import io
import json

import pytest

from mattermatch.artifacts import ArtifactError, _install_bytes
from mattermatch.cli import EXIT_OK, EXIT_USAGE_OR_INVENTORY, main
from mattermatch.decoder import Detection

VALID = "MT:M5L90MP500K64J00000"


class Fake:
    def __init__(self, values):
        self.values = values

    def decode(self, path):
        return self.values.get(path, [])


def test_unmatched_csv_retains_derived_pairing_code(tmp_path):
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("code,descriptor\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert main(
        ["--inventory", str(inventory), "missing.png"],
        decoder=Fake({"missing.png": [Detection(VALID)]}),
        stdout=out,
        stderr=err,
    ) == EXIT_OK
    assert out.getvalue() == (
        "qr_code,descriptor,pairing_code\n"
        f"{VALID},,00204800002\n"
    )
    assert "not in inventory" in err.getvalue()


def test_inventory_normalize_explicit_headerless_reversed_and_repair(tmp_path):
    source = tmp_path / "sheet.csv"
    output = tmp_path / "canonical.csv"
    # Google Sheets-style conversion of a 21-digit code: one leading zero was
    # dropped.  The candidate is checksum-valid only at the declared length.
    source.write_text("Lamp,41571222431953386307\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert main(
        [
            "inventory", "normalize", str(source), "--columns", "descriptor,code",
            "--no-header", "--pad-leading-zeroes", "--output", str(output),
        ], stdout=out, stderr=err
    ) == EXIT_OK
    assert output.read_text(encoding="utf-8") == "code,descriptor\n041571222431953386307,Lamp\n"
    assert "repaired leading zeroes" in err.getvalue()
    assert out.getvalue() == ""


def test_inventory_normalize_rejects_ambiguous_and_preserves_destination(tmp_path):
    source = tmp_path / "sheet.csv"
    output = tmp_path / "canonical.csv"
    source.write_text("descriptor,code\nLamp,204800002\n", encoding="utf-8")
    output.write_text("old\n", encoding="utf-8")
    err = io.StringIO()
    assert main(
        [
            "inventory", "normalize", str(source), "--columns", "descriptor,code",
            "--pad-leading-zeroes", "--output", str(output),
        ], stderr=err
    ) == EXIT_USAGE_OR_INVENTORY
    assert "ambiguous" in err.getvalue()
    assert output.read_text(encoding="utf-8") == "old\n"


def test_jsonl_has_source_metadata_and_escaping(tmp_path):
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("code,descriptor\n00204800002,known \\\"lamp\\\"\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert main(
        ["--inventory", str(inventory), "--format", "jsonl", "a.png"],
        decoder=Fake({"a.png": [Detection(VALID, [(2, 4), (5, 4), (2, 9), (5, 9)])]}),
        stdout=out,
        stderr=err,
    ) == EXIT_OK
    record = json.loads(out.getvalue())
    assert record == {
        "source_image": "a.png",
        "detection_ordinal": 1,
        "bounding_box": [2.0, 4.0, 5.0, 9.0],
        "qr_code": VALID,
        "pairing_code": "00204800002",
        "descriptor": 'known \\"lamp\\"',
        "status": "matched",
    }
    assert err.getvalue() == ""


def test_inventory_check_reports_row_reason(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text("code,descriptor\n00204800001,lamp\n", encoding="utf-8")
    err = io.StringIO()
    assert main(["inventory", "check", str(source)], stderr=err) == EXIT_USAGE_OR_INVENTORY
    assert "row 2" in err.getvalue()
    assert "checksum" in err.getvalue()


def test_inventory_repair_diagnostics_have_one_omission_summary(tmp_path):
    from mattermatch.inventory import normalize_inventory_rows
    from mattermatch.matter import verhoeff_check_digit

    source = tmp_path / "repairs.csv"
    rows = []
    for index in range(150):
        body = f"{index:019d}"
        canonical = "0" + body + verhoeff_check_digit("0" + body)
        rows.append(f"{canonical[1:]},row-{index}")
    source.write_text("code,descriptor\n" + "\n".join(rows) + "\n", encoding="utf-8")
    normalized, repairs = normalize_inventory_rows(source, pad_leading_zeroes=True)
    assert len(normalized) == 150
    assert len(repairs) == 101
    assert repairs[-1] == "additional inventory repair diagnostics omitted"


def test_artifact_collision_never_replaces_existing_bytes(tmp_path):
    destination = tmp_path / "artifact.bin"
    destination.write_bytes(b"winner")
    with pytest.raises(ArtifactError):
        _install_bytes(b"other", destination)
    assert destination.read_bytes() == b"winner"
    _install_bytes(b"winner", destination)
