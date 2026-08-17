import pytest

from mattermatch.matter import (
    MalformedMatter,
    SetupPayload,
    base38_decode,
    base38_encode,
    derive_manual_code,
    parse_matter_payload,
    verhoeff_check_digit,
    verhoeff_validate,
)
from mattermatch.matter import _TLVReader



def packed_payload(*, vendor=12, product=1, flow=0, rendezvous=1, discriminator=128, pin=2048, padding=0):
    values = [(0, 3), (vendor, 16), (product, 16), (flow, 2), (rendezvous, 8),
              (discriminator, 12), (pin, 27), (padding, 4)]
    data = bytearray(11)
    offset = 0
    for value, width in values:
        for bit in range(width):
            if value & (1 << bit):
                data[(offset + bit) // 8] |= 1 << ((offset + bit) % 8)
        offset += width
    return data


def qr(**kwargs):
    return "MT:" + base38_encode(packed_payload(**kwargs))


def test_base38_vectors_and_roundtrip():
    assert base38_encode(b"\n") == "A0"
    assert base38_encode(b"\n\n") == "OT10"
    assert base38_encode(b"\n\n\n") == "-N.B0"
    for data in (b"", b"\0", b"abc", bytes(range(11))):
        assert base38_decode(base38_encode(data)) == data


@pytest.mark.parametrize("text", ["0", "000", "000000", "00000000000"])
def test_base38_rejects_invalid_chunk_lengths(text):
    with pytest.raises(ValueError):
        base38_decode(text)


@pytest.mark.parametrize("text", ["ZZ", "ZZZZ", "ZZZZZ"])
def test_base38_rejects_chunk_overflow(text):
    with pytest.raises(ValueError, match="overflow"):
        base38_decode(text)


def test_tlv_tag_controls_consume_chip_widths():
    for control, width in ((0x40, 2), (0x60, 4), (0x80, 2),
                           (0xA0, 4), (0xC0, 6), (0xE0, 8)):
        reader = _TLVReader(bytes((control | 0x14,)) + bytes(width))
        assert reader.root().type_code == 0x14
        assert reader.index == 1 + width


def test_official_qr_and_high_four_bit_discriminator_manual_code():
    payload = parse_matter_payload("MT:M5L90MP500K64J00000")[0]
    assert payload.discriminator == 128
    assert payload.short_discriminator == 0
    assert derive_manual_code(payload) == "00204800002"
    # This is deliberately a discriminator whose high four bits differ from
    # its low four bits; the short discriminator is the high four bits.
    assert derive_manual_code(parse_matter_payload(qr(discriminator=0xABC, pin=12349876))[0]).startswith("2")


def test_nonstandard_manual_code_and_leading_zero_fields():
    payload = parse_matter_payload(qr(flow=1, vendor=1, product=1, discriminator=0xF00, pin=12349876))[0]
    code = derive_manual_code(payload)
    assert len(code) == 21
    assert code.startswith("76")
    assert verhoeff_validate(code)


def test_concatenated_payloads_are_in_order():
    value = "MT:M5L90MP500K64J00000*M5L90U.D010K4J00000"
    payloads = parse_matter_payload(value)
    assert [p.setup_pin for p in payloads] == [2048, 2049]


def test_invalid_payload_fields_and_optional_tlv():
    with pytest.raises(MalformedMatter):
        parse_matter_payload(qr(padding=1))
    with pytest.raises(MalformedMatter):
        parse_matter_payload("MT:M5L90MP500K64J0000!")
    raw = packed_payload() + bytes((0x15, 0x2C, 0, 3)) + b"abc" + bytes((0x18,))
    payload = parse_matter_payload("MT:" + base38_encode(raw))[0]
    assert payload.optional[0] == {"tag": 0, "type": "string", "value": "abc"}


def optional_qr(tlv: bytes) -> str:
    return "MT:" + base38_encode(packed_payload() + tlv)


@pytest.mark.parametrize(
    "tlv",
    [
        # Anonymous structure child.
        bytes((0x15, 0x00, 1, 0x18)),
        # Context-tagged array child.
        bytes((0x15, 0x36, 1, 0x24, 2, 1, 0x18, 0x18)),
        # Tagged terminator.
        bytes((0x15, 0x2C, 0, 0x38)),
    ],
)
def test_optional_tlv_enforces_chip_container_tags_and_terminators(tlv):
    with pytest.raises(MalformedMatter):
        parse_matter_payload(optional_qr(tlv))


def test_optional_tlv_lists_allow_tagged_and_anonymous_elements():
    # Lists may mix the two element tag forms; unlike arrays, they do not
    # impose a single tag kind on their children.
    tlv = bytes((
        0x15, 0x37, 1,
        0x20, 2, 7,
        0x00, 8,
        0x18, 0x18,
    ))
    assert parse_matter_payload(optional_qr(tlv))[0].optional == ()


def test_concatenated_parse_is_atomic_on_late_failure():
    valid = base38_encode(packed_payload(pin=2048))
    malformed = base38_encode(packed_payload(pin=2049))[:-1] + "!"
    with pytest.raises(MalformedMatter):
        parse_matter_payload("MT:" + valid + "*" + malformed)


def test_overlong_matter_text_is_malformed():
    with pytest.raises(MalformedMatter):
        parse_matter_payload("MT:" + "0" * 4094)
    with pytest.raises(MalformedMatter):
        parse_matter_payload(" MT:M5L90MP500K64J00000 " + " " * 4096)


def test_verhoeff_vectors():
    assert verhoeff_check_digit("2363") == "4"
    assert verhoeff_validate("23634")
    assert not verhoeff_validate("23635")
