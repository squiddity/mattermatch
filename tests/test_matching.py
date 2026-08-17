from mattermatch.decoder import Detection, order_detections
from mattermatch.diagnostics import Diagnostics
from mattermatch.inventory import Inventory
from mattermatch.matching import process_images

VALID = "MT:M5L90MP500K64J00000"


class FakeDecoder:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def decode(self, filename):
        self.calls.append(filename)
        value = self.values.get(filename, [])
        if isinstance(value, Exception):
            raise value
        return value


def pos(x, y):
    return [(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)]


def test_geometry_order_duplicate_preservation_and_warnings():
    decoder = FakeDecoder({
        "b.png": [Detection(VALID, pos(20, 20)), Detection("other", pos(1, 1)), Detection(VALID, pos(2, 2))],
        "a.png": [Detection(VALID, pos(1, 1))],
    })
    diagnostics = Diagnostics()
    result = process_images(
        ["b.png", "a.png"],
        Inventory({"00204800002": "lamp"}),
        decoder,
        diagnostics,
    )
    assert decoder.calls == ["b.png", "a.png"]
    assert [row.qr_code for row in result.rows] == [VALID, VALID, VALID]
    assert [row.descriptor for row in result.rows] == ["lamp", "lamp", "lamp"]
    text = "\n".join(diagnostics.lines)
    assert text.count("duplicate QR") == 2
    assert "non-Matter QR" in text


def test_invalid_duplicate_is_not_tracked_as_duplicate():
    invalid = "MT:" + "0" * 4094
    decoder = FakeDecoder({"one.png": [Detection(invalid), Detection(invalid)]})
    diagnostics = Diagnostics()
    result = process_images(["one.png"], Inventory({}), decoder, diagnostics)
    assert result.rows == ()
    assert sum("malformed Matter QR" in line for line in diagnostics.lines) == 2
    assert not any("duplicate QR" in line for line in diagnostics.lines)


def test_overlong_decoded_text_is_one_malformed_warning_and_no_row():
    invalid = "MT:" + "0" * 4094
    decoder = FakeDecoder({"one.png": [Detection(invalid)]})
    diagnostics = Diagnostics()
    result = process_images(["one.png"], Inventory({}), decoder, diagnostics)
    assert result.rows == ()
    assert len(diagnostics.lines) == 1
    assert "malformed Matter QR" in diagnostics.lines[0]


def test_unmatched_and_strict_count():
    decoder = FakeDecoder({"one.png": [Detection(VALID)]})
    diagnostics = Diagnostics()
    result = process_images(
        ["one.png"], Inventory({}), decoder, diagnostics,
        expected_qr_count=2, strict_count=True,
    )
    assert result.strict_count_mismatch
    assert result.rows[0].descriptor == ""
    assert result.rows[0].pairing_code == ""
    assert any("expected 2" in line for line in diagnostics.lines)


def test_image_failure_recovers_later_images():
    decoder = FakeDecoder({"bad.png": OSError(), "good.png": [Detection(VALID)]})
    diagnostics = Diagnostics()
    result = process_images(["bad.png", "good.png"], Inventory({"00204800002": "ok"}), decoder, diagnostics)
    assert result.image_failure
    assert len(result.rows) == 1
    assert any("could not be read" in line for line in diagnostics.lines)
