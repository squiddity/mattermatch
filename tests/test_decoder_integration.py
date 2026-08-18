import pytest

from mattermatch.decoder import ZXingCppDecoder, order_detections
from mattermatch.matter import base38_encode


def _payload(*, pin: int, discriminator: int) -> str:
    values = [(0, 3), (12, 16), (1, 16), (0, 2), (1, 8),
              (discriminator, 12), (pin, 27), (0, 4)]
    raw = bytearray(11)
    offset = 0
    for value, width in values:
        for bit in range(width):
            if value & (1 << bit):
                raw[(offset + bit) // 8] |= 1 << ((offset + bit) % 8)
        offset += width
    return "MT:" + base38_encode(raw)


@pytest.mark.integration
def test_zxing_cpp_retry_preprocessing_retains_geometry_and_payload(tmp_path):
    zxingcpp = pytest.importorskip("zxingcpp")
    pytest.importorskip("PIL")
    np = pytest.importorskip("numpy")
    if not hasattr(zxingcpp, "create_barcode"):
        pytest.skip("zxing-cpp build does not expose barcode generation")
    from PIL import Image

    text = _payload(pin=2048, discriminator=128)
    generated = np.asarray(
        zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode).to_image(scale=5)
    )
    path = tmp_path / "retry.png"
    Image.fromarray(generated).save(path)
    decoder = ZXingCppDecoder()
    detections = decoder.decode_with_preprocessing(path, expected_qr_count=1)
    assert [item.text for item in order_detections(detections)] == [text]
    assert detections[0].position is not None


@pytest.mark.integration
def test_zxing_cpp_decodes_generated_qr(tmp_path):
    zxingcpp = pytest.importorskip("zxingcpp")
    pytest.importorskip("PIL")
    np = pytest.importorskip("numpy")
    if not hasattr(zxingcpp, "create_barcode"):
        pytest.skip("zxing-cpp build does not expose barcode generation")
    from PIL import Image

    expected = [_payload(pin=2048 + index, discriminator=128 + index) for index in range(12)]
    tile_size = 220
    canvas = np.full((tile_size * 3, tile_size * 4), 255, dtype=np.uint8)
    for index, text in enumerate(expected):
        image = np.asarray(
            zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
            .to_image(scale=5)
        )
        top = (index // 4) * tile_size + (tile_size - image.shape[0]) // 2
        left = (index % 4) * tile_size + (tile_size - image.shape[1]) // 2
        canvas[top : top + image.shape[0], left : left + image.shape[1]] = image

    path = tmp_path / "generated-grid.png"
    Image.fromarray(canvas).save(path)
    decoder = ZXingCppDecoder()
    first = [item.text for item in order_detections(decoder.decode(path))]
    second = [item.text for item in order_detections(decoder.decode(path))]
    assert len(first) == 12
    assert first == second == expected
