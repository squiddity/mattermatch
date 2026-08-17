import struct
import zlib

import pytest

from mattermatch.decoder import ImageDecodeError, ZXingCppDecoder


def _png_header(width: int, height: int) -> bytes:
    data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(data))
        + b"IHDR"
        + data
        + struct.pack(">I", zlib.crc32(b"IHDR" + data) & 0xFFFFFFFF)
        + struct.pack(">I", 0)
        + b"IEND"
        + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    )


def test_image_dimensions_are_rejected_before_pixel_allocation(tmp_path):
    path = tmp_path / "oversized.png"
    # This is only a header; loading pixels would fail or allocate.  Pillow's
    # lazy header read lets the adapter reject it on dimensions alone.
    path.write_bytes(_png_header(7000, 7000))
    with pytest.raises(ImageDecodeError, match="dimensions exceed"):
        ZXingCppDecoder().decode(path)


def test_decoder_retains_complete_native_text(tmp_path, monkeypatch):
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    path = tmp_path / "tiny.png"
    Image.new("RGB", (2, 2), "white").save(path)
    text = "MT:" + "0" * 4094

    class Result:
        position = None

        def __init__(self):
            self.text = text

    monkeypatch.setattr(zxingcpp, "read_barcodes", lambda *args, **kwargs: [Result()])
    detections = ZXingCppDecoder().decode(path)
    assert detections[0].text == text
