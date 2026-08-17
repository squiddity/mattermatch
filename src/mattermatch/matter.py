"""Matter setup-payload parsing and manual pairing-code generation.

This module intentionally contains no image or command-line dependencies.  The
wire format follows the Project CHIP QR-code setup payload implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import Any

BASE38_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-."
MAX_PAYLOAD_TEXT = 4096
MAX_OPTIONAL_TLV_BYTES = 1024 * 1024

# Project CHIP's Verhoeff-10 tables.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


class MatterPayloadError(ValueError):
    """Base class for a QR payload that cannot be consumed."""


class NonMatter(MatterPayloadError):
    """Decoded text is not a Matter QR payload."""


class MalformedMatter(MatterPayloadError):
    """Decoded text has the Matter prefix but invalid payload data."""


class UnsupportedPayload(MalformedMatter):
    """Payload is well-formed but uses a version this implementation cannot use."""


def verhoeff_check_digit(digits: str) -> str:
    """Return the Verhoeff check digit for an ASCII decimal string."""
    if not isinstance(digits, str) or not digits or any(c not in "0123456789" for c in digits):
        raise ValueError("Verhoeff input must be non-empty ASCII decimal digits")
    checksum = 0
    for index, char in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[(index + 1) % 8][ord(char) - 48]]
    return str(_VERHOEFF_INV[checksum])


def verhoeff_validate(digits: str) -> bool:
    """Validate a complete decimal string, including its final check digit."""
    if not isinstance(digits, str) or len(digits) < 2 or any(c not in "0123456789" for c in digits):
        return False
    checksum = 0
    for index, char in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[index % 8][ord(char) - 48]]
    return checksum == 0


# Friendly aliases used by callers that refer to the algorithm by its CHIP name.
compute_verhoeff_check_digit = verhoeff_check_digit
validate_verhoeff = verhoeff_validate


def base38_encode(data: bytes | bytearray | memoryview) -> str:
    """Encode bytes using Matter's little-endian, fixed-size Base38 chunks."""
    raw = bytes(data)
    result: list[str] = []
    for offset in range(0, len(raw), 3):
        chunk = raw[offset : offset + 3]
        value = sum(byte << (8 * i) for i, byte in enumerate(chunk))
        count = {1: 2, 2: 4, 3: 5}[len(chunk)]
        for _ in range(count):
            value, digit = divmod(value, 38)
            result.append(BASE38_ALPHABET[digit])
        if value:
            raise ValueError("value does not fit in Base38 chunk")
    return "".join(result)


def base38_decode(text: str, *, max_chars: int = MAX_PAYLOAD_TEXT) -> bytes:
    """Decode Matter Base38, rejecting invalid chunk lengths and overflow."""
    if not isinstance(text, str):
        raise ValueError("Base38 input must be text")
    if len(text) > max_chars:
        raise ValueError("invalid Base38 length")
    if not text:
        return b""
    values = {char: index for index, char in enumerate(BASE38_ALPHABET)}
    remaining = len(text)
    offset = 0
    output = bytearray()
    while remaining:
        if remaining >= 5:
            chars, byte_count = 5, 3
        elif remaining == 4:
            chars, byte_count = 4, 2
        elif remaining == 2:
            chars, byte_count = 2, 1
        else:
            raise ValueError("invalid Base38 chunk length")
        value = 0
        for char in reversed(text[offset : offset + chars]):
            try:
                digit = values[char]
            except KeyError as exc:
                raise ValueError("invalid Base38 character") from exc
            value = value * 38 + digit
        for _ in range(byte_count):
            output.append(value & 0xFF)
            value >>= 8
        if value:
            raise ValueError("Base38 chunk overflow")
        offset += chars
        remaining -= chars
    return bytes(output)


# Short names make vectors/readers pleasant while preserving explicit APIs.
encode_base38 = base38_encode
decode_base38 = base38_decode


@dataclass(frozen=True)
class SetupPayload:
    """The fields needed to derive a Matter manual pairing code."""

    version: int
    vendor_id: int
    product_id: int
    commissioning_flow: int
    rendezvous_information: int
    discriminator: int
    setup_pin: int
    optional: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    # C++/spec spellings are useful for users comparing vectors with CHIP.
    @property
    def vendorID(self) -> int:
        return self.vendor_id

    @property
    def productID(self) -> int:
        return self.product_id

    @property
    def commissioningFlow(self) -> int:
        return self.commissioning_flow

    @property
    def rendezvousInformation(self) -> int:
        return self.rendezvous_information

    @property
    def setUpPINCode(self) -> int:
        return self.setup_pin

    @property
    def short_discriminator(self) -> int:
        return (self.discriminator >> 8) & 0xF

    @property
    def discriminator_short(self) -> int:
        return self.short_discriminator

    def manual_code(self) -> str:
        return derive_manual_code(self)

    def pairing_code(self) -> str:
        return self.manual_code()


@dataclass(frozen=True)
class _TLVElement:
    type_code: int
    tag_kind: str
    tag_number: int | None
    value: Any = None
    children: tuple["_TLVElement", ...] = ()


class _TLVReader:
    """Small strict Matter-TLV reader used only for QR optional data."""

    def __init__(self, data: bytes):
        if len(data) > MAX_OPTIONAL_TLV_BYTES:
            raise ValueError("optional data is too large")
        self.data = data
        self.index = 0

    def _take(self, count: int) -> bytes:
        end = self.index + count
        if end > len(self.data):
            raise ValueError("truncated TLV")
        value = self.data[self.index:end]
        self.index = end
        return value

    def _tag(self, control: int) -> tuple[str, int | None]:
        kind = control & 0xE0
        if kind == 0x00:
            return "anonymous", None
        if kind == 0x20:
            return "context", self._take(1)[0]
        # CHIP TLV tag controls encode the total tag width, not a profile/tag
        # pair for every control.  The six non-anonymous forms are 2, 4, 2,
        # 4, 6, and 8 bytes respectively.
        widths = {0x40: 2, 0x60: 4, 0x80: 2, 0xA0: 4, 0xC0: 6, 0xE0: 8}
        if kind in widths:
            return "profile", int.from_bytes(self._take(widths[kind]), "little")
        raise ValueError("invalid TLV tag control")

    def element(self) -> _TLVElement:
        control = self._take(1)[0]
        type_code = control & 0x1F
        if type_code > 0x18:
            raise ValueError("invalid TLV type")
        tag_kind, tag_number = self._tag(control)

        if type_code in (0x00, 0x01, 0x02, 0x03):
            width = 1 << (type_code & 3)
            value = int.from_bytes(self._take(width), "little", signed=True)
            return _TLVElement(type_code, tag_kind, tag_number, value)
        if type_code in (0x04, 0x05, 0x06, 0x07):
            width = 1 << (type_code & 3)
            value = int.from_bytes(self._take(width), "little", signed=False)
            return _TLVElement(type_code, tag_kind, tag_number, value)
        if type_code in (0x08, 0x09):
            return _TLVElement(type_code, tag_kind, tag_number, type_code == 0x09)
        if type_code in (0x0A, 0x0B):
            width = 4 if type_code == 0x0A else 8
            value = struct.unpack("<f" if width == 4 else "<d", self._take(width))[0]
            return _TLVElement(type_code, tag_kind, tag_number, value)
        if 0x0C <= type_code <= 0x13:
            width = 1 << (type_code & 3)
            length = int.from_bytes(self._take(width), "little")
            if length > MAX_OPTIONAL_TLV_BYTES or length > len(self.data) - self.index:
                raise ValueError("invalid TLV string length")
            value = self._take(length)
            if type_code <= 0x0F:
                try:
                    value = value.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError("invalid TLV UTF-8") from exc
            return _TLVElement(type_code, tag_kind, tag_number, value)
        if type_code == 0x14:
            return _TLVElement(type_code, tag_kind, tag_number)
        if 0x15 <= type_code <= 0x17:
            children: list[_TLVElement] = []
            while True:
                if self.index >= len(self.data):
                    raise ValueError("unterminated TLV container")
                if self.data[self.index] & 0x1F == 0x18:
                    end = self._take(1)[0]
                    if end & 0xE0:
                        raise ValueError("tagged TLV container terminator")
                    break
                children.append(self.element())
                if len(children) > MAX_OPTIONAL_TLV_BYTES:
                    raise ValueError("too many TLV elements")
            return _TLVElement(type_code, tag_kind, tag_number, children=tuple(children))
        # 0x18 is only legal as consumed above, never as an element itself.
        raise ValueError("unexpected TLV container terminator")

    def root(self) -> _TLVElement:
        if not self.data:
            raise ValueError("empty optional TLV")
        root = self.element()
        if self.index != len(self.data):
            raise ValueError("trailing TLV data")
        return root


def _validate_container_tags(element: _TLVElement) -> None:
    """Apply CHIP's tag rules to every parsed container and its descendants."""
    if element.type_code == 0x15:  # Structure elements are always tagged.
        if any(child.tag_kind == "anonymous" for child in element.children):
            raise ValueError("anonymous element in TLV structure")
    elif element.type_code == 0x16:  # Array elements are always anonymous.
        if any(child.tag_kind != "anonymous" for child in element.children):
            raise ValueError("tagged element in TLV array")
    elif element.type_code == 0x17:
        # CHIP lists deliberately allow either tagged or anonymous elements
        # (and duplicate tags).
        pass
    else:
        return
    # Recurse so nested structures and arrays are checked as well.
    for child in element.children:
        _validate_container_tags(child)


def _validate_optional(data: bytes) -> tuple[dict[str, Any], ...]:
    if not data:
        return ()
    root = _TLVReader(data).root()
    if root.type_code != 0x15 or root.tag_kind != "anonymous":
        raise ValueError("optional TLV root is not an anonymous structure")
    _validate_container_tags(root)
    optional: list[dict[str, Any]] = []
    for item in root.children:
        # CHIP's parser skips non-scalar entries, but still validates their TLV
        # structure.  Preserve that behavior for containers and nulls.
        if not (0x00 <= item.type_code <= 0x07 or 0x0C <= item.type_code <= 0x13):
            continue
        if item.tag_kind != "context" or item.tag_number is None:
            raise ValueError("optional scalar does not use a context tag")
        tag = item.tag_number
        if 0x0C <= item.type_code <= 0x0F:
            optional.append({"tag": tag, "type": "string", "value": item.value})
        elif item.type_code in (0x00, 0x01, 0x02, 0x03):
            # Vendor numeric fields are signed int32; common serial is uint32.
            if tag < 0x80 or tag == 0 or item.type_code == 0x03:
                raise ValueError("invalid signed optional integer")
            if not -(1 << 31) <= item.value <= (1 << 31) - 1:
                raise ValueError("vendor integer does not fit int32")
            optional.append({"tag": tag, "type": "int32", "value": item.value})
        elif item.type_code in (0x04, 0x05, 0x06, 0x07):
            if tag != 0 or item.type_code == 0x07 or item.value > 0xFFFFFFFF:
                raise ValueError("invalid optional unsigned integer")
            optional.append({"tag": tag, "type": "uint32", "value": item.value})
        else:
            # The parser's retrieveOptionalInfos ignores other scalar types;
            # all of them have nevertheless been fully and safely consumed.
            continue
    return tuple(optional)


def _read_bits(data: bytes, index: int, width: int) -> tuple[int, int]:
    if index + width > len(data) * 8:
        raise ValueError("payload is too short")
    value = 0
    for bit in range(width):
        if data[(index + bit) // 8] & (1 << ((index + bit) % 8)):
            value |= 1 << bit
    return value, index + width


def _validate_payload_fields(payload: SetupPayload) -> None:
    if payload.version != 0:
        raise UnsupportedPayload(f"unsupported Matter payload version {payload.version}")
    if payload.commissioning_flow not in (0, 1, 2):
        raise MalformedMatter("invalid commissioning flow")
    if payload.rendezvous_information & ~0x3F:
        raise MalformedMatter("invalid rendezvous information")
    # 0 is the unspecified vendor; operational values include CSA IDs and the
    # four test IDs, and stop at 0xFFF4.  0xFFFF is explicitly not specified.
    if payload.vendor_id != 0 and payload.vendor_id > 0xFFF4:
        raise MalformedMatter("invalid vendor ID")
    if payload.product_id == 0 and payload.vendor_id != 0:
        raise MalformedMatter("invalid product ID")
    pin = payload.setup_pin
    if pin == 0 or pin > 99_999_998 or pin in {
        11_111_111,
        22_222_222,
        33_333_333,
        44_444_444,
        55_555_555,
        66_666_666,
        77_777_777,
        88_888_888,
        12_345_678,
        87_654_321,
    }:
        raise MalformedMatter("invalid setup PIN")


def parse_matter_payload(text: str) -> list[SetupPayload]:
    """Parse one Matter QR result into one payload per ``*`` chunk."""
    if not isinstance(text, str):
        raise NonMatter("decoded value is not text")
    normalized = text.strip()
    if not normalized.startswith("MT:"):
        raise NonMatter("not a Matter QR payload")
    # Bound the complete decoded result as well as its normalized form.  A
    # hostile result must not evade the limit by hiding bytes in surrounding
    # whitespace, and the decoder must never truncate it before this check.
    if len(text) > MAX_PAYLOAD_TEXT or len(normalized) > MAX_PAYLOAD_TEXT:
        raise MalformedMatter("payload is too long")
    representation = normalized[3:]
    chunks = representation.split("*")
    if not representation or any(not chunk for chunk in chunks):
        raise MalformedMatter("empty Matter payload chunk")
    payloads: list[SetupPayload] = []
    try:
        for chunk in chunks:
            raw = base38_decode(chunk, max_chars=MAX_PAYLOAD_TEXT)
            # Mandatory data is 88 bits (11 bytes); remaining bytes are TLV.
            if len(raw) < 11:
                raise ValueError("payload is too short")
            index = 0
            version, index = _read_bits(raw, index, 3)
            vendor, index = _read_bits(raw, index, 16)
            product, index = _read_bits(raw, index, 16)
            flow, index = _read_bits(raw, index, 2)
            rendezvous, index = _read_bits(raw, index, 8)
            discriminator, index = _read_bits(raw, index, 12)
            setup_pin, index = _read_bits(raw, index, 27)
            padding, index = _read_bits(raw, index, 4)
            if padding:
                raise ValueError("payload padding is not zero")
            optional = _validate_optional(raw[11:])
            item = SetupPayload(
                version=version,
                vendor_id=vendor,
                product_id=product,
                commissioning_flow=flow,
                rendezvous_information=rendezvous,
                discriminator=discriminator,
                setup_pin=setup_pin,
                optional=optional,
            )
            _validate_payload_fields(item)
            payloads.append(item)
    except UnsupportedPayload:
        raise
    except MalformedMatter:
        raise
    except (ValueError, struct.error, RecursionError) as exc:
        raise MalformedMatter("invalid Matter payload") from exc
    return payloads


# Names commonly used by integrations.
parse_payload = parse_matter_payload
parse_qr_payload = parse_matter_payload


def derive_manual_code(payload: SetupPayload) -> str:
    """Generate CHIP's canonical 11- or 21-digit manual pairing code."""
    short_discriminator = (payload.discriminator >> 8) & 0xF
    chunk1 = ((short_discriminator >> 2) & 0x3) | ((payload.commissioning_flow != 0) << 2)
    chunk2 = (payload.setup_pin & 0x3FFF) | ((short_discriminator & 0x3) << 14)
    chunk3 = (payload.setup_pin >> 14) & 0x1FFF
    base = f"{chunk1:01d}{chunk2:05d}{chunk3:04d}"
    if payload.commissioning_flow != 0:
        base += f"{payload.vendor_id:05d}{payload.product_id:05d}"
    return base + verhoeff_check_digit(base)


generate_manual_code = derive_manual_code
MatterPayload = SetupPayload
