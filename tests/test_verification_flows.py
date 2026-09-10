"""Pinned synthetic vectors and chip-tool-style logs; no tool/network needed.

Reference revision: project-chip/connectedhomeip v1.4.0.0:
https://github.com/project-chip/connectedhomeip/blob/v1.4.0.0/src/setup_payload/ManualSetupPayloadParser.cpp
(populatePayload maps long codes to kCustom, not kUserActionRequired).
https://github.com/project-chip/connectedhomeip/blob/v1.4.0.0/examples/chip-tool/commands/payload/SetupPayloadParseCommand.cpp
(Print supplies the labels/spacing/flow names below; timestamps are synthetic).

All vectors have version=0, VID=65521, PID=32768, discovery=2 (BLE),
long discriminator=3840 (short=15), PIN=20202021. Only QR flow bits vary.
Independently packed 88-bit, little-endian payload bytes for flows 0/1/2:
  88ff07004400e04b846802 / 88ff07004c00e04b846802 / 88ff07005400e04b846802
Manual decimal chunks are 3|49701|1233 (standard) or
7|49701|1233|65521|32768 (nonstandard), with Verhoeff digits 2 and 7.
The literals were calculated separately from mattermatch's encoder/generator;
fixtures deliberately do not derive expected tool fields from the local parser.
"""

import pytest

import mattermatch.verification as verification


VECTORS = (
    (0, "MT:Y.K9042C00KA0648G00", "34970112332"),
    (1, "MT:Y.K900ID00KA0648G00", "749701123365521327687"),
    (2, "MT:Y.K90YXE00KA0648G00", "749701123365521327687"),
)

QR_LOGS = (
    b"""[1720000000.000001][1234:1234] CHIP: [SPL] Version:             0
[1720000000.000002][1234:1234] CHIP: [SPL] VendorID:            65521
[1720000000.000003][1234:1234] CHIP: [SPL] ProductID:           32768
[1720000000.000004][1234:1234] CHIP: [SPL] Custom flow:         0    (STANDARD)
[1720000000.000005][1234:1234] CHIP: [SPL] Discovery Bitmask:   0x02 (BLE)
[1720000000.000006][1234:1234] CHIP: [SPL] Long discriminator:  3840   (0xf00)
[1720000000.000007][1234:1234] CHIP: [SPL] Passcode:            20202021
""",
    b"""[1720000000.000001][1234:1234] CHIP: [SPL] Version:             0
[1720000000.000002][1234:1234] CHIP: [SPL] VendorID:            65521
[1720000000.000003][1234:1234] CHIP: [SPL] ProductID:           32768
[1720000000.000004][1234:1234] CHIP: [SPL] Custom flow:         1    (USER ACTION REQUIRED)
[1720000000.000005][1234:1234] CHIP: [SPL] Discovery Bitmask:   0x02 (BLE)
[1720000000.000006][1234:1234] CHIP: [SPL] Long discriminator:  3840   (0xf00)
[1720000000.000007][1234:1234] CHIP: [SPL] Passcode:            20202021
""",
    b"""[1720000000.000001][1234:1234] CHIP: [SPL] Version:             0
[1720000000.000002][1234:1234] CHIP: [SPL] VendorID:            65521
[1720000000.000003][1234:1234] CHIP: [SPL] ProductID:           32768
[1720000000.000004][1234:1234] CHIP: [SPL] Custom flow:         2    (CUSTOM)
[1720000000.000005][1234:1234] CHIP: [SPL] Discovery Bitmask:   0x02 (BLE)
[1720000000.000006][1234:1234] CHIP: [SPL] Long discriminator:  3840   (0xf00)
[1720000000.000007][1234:1234] CHIP: [SPL] Passcode:            20202021
""",
)

MANUAL_STANDARD_LOG = b"""[1720000001.000001][1235:1235] CHIP: [SPL] Version:             0
[1720000001.000002][1235:1235] CHIP: [SPL] VendorID:            0
[1720000001.000003][1235:1235] CHIP: [SPL] ProductID:           0
[1720000001.000004][1235:1235] CHIP: [SPL] Custom flow:         0    (STANDARD)
[1720000001.000005][1235:1235] CHIP: [SPL] Discovery Bitmask:   UNKNOWN
[1720000001.000006][1235:1235] CHIP: [SPL] Short discriminator: 15   (0xf)
[1720000001.000007][1235:1235] CHIP: [SPL] Passcode:            20202021
"""

# Both nonstandard QR flows encode this SAME manual code and parse as CUSTOM.
MANUAL_CUSTOM_LOG = b"""[1720000001.000001][1235:1235] CHIP: [SPL] Version:             0
[1720000001.000002][1235:1235] CHIP: [SPL] VendorID:            65521
[1720000001.000003][1235:1235] CHIP: [SPL] ProductID:           32768
[1720000001.000004][1235:1235] CHIP: [SPL] Custom flow:         2    (CUSTOM)
[1720000001.000005][1235:1235] CHIP: [SPL] Discovery Bitmask:   UNKNOWN
[1720000001.000006][1235:1235] CHIP: [SPL] Short discriminator: 15   (0xf)
[1720000001.000007][1235:1235] CHIP: [SPL] Passcode:            20202021
"""
MANUAL_LOGS = (MANUAL_STANDARD_LOG, MANUAL_CUSTOM_LOG, MANUAL_CUSTOM_LOG)


def _stub_chip_tool(monkeypatch, qr, code, qr_log, manual_log):
    calls = []
    outputs = {qr: qr_log, code: manual_log}

    def run(args):
        calls.append(args)
        assert args[:3] == ["chip-tool", "payload", "parse-setup-payload"]
        return "pass", outputs[args[3]], None

    monkeypatch.setattr(verification, "_bounded_run", run)
    return calls


@pytest.mark.parametrize("flow,qr,code", VECTORS)
def test_chip_tool_accepts_independent_flow_representations(monkeypatch, flow, qr, code):
    calls = _stub_chip_tool(monkeypatch, qr, code, QR_LOGS[flow], MANUAL_LOGS[flow])
    result = verification.verify_pair(qr, code, chip_tool="chip-tool")
    assert result.passed
    assert result.chip_tool_status == "pass"
    assert result.chip_tool_reason is None
    assert result.commissioning_flow == flow  # Preserve the QR's exact flow.
    assert calls == [
        ["chip-tool", "payload", "parse-setup-payload", qr],
        ["chip-tool", "payload", "parse-setup-payload", code],
    ]


@pytest.mark.parametrize(
    "flow,representation,bad_flow",
    [
        (flow, representation, bad_flow)
        for flow, manual_flow in ((0, 0), (1, 2), (2, 2))
        for representation, expected in (("qr", flow), ("manual", manual_flow))
        for bad_flow in (0, 1, 2, 3)
        if bad_flow != expected
    ],
)
def test_chip_tool_rejects_wrong_flow(monkeypatch, flow, representation, bad_flow):
    _, qr, code = VECTORS[flow]
    logs = {"qr": QR_LOGS[flow], "manual": MANUAL_LOGS[flow]}
    # Alter only the selected tool's flow line; all PIN/discriminator/ID fields
    # remain correct. In particular QR 1/manual 1 must NOT be accepted.
    logs[representation] = b"\n".join(
        line.split(b"Custom flow:")[0] + f"Custom flow:         {bad_flow}".encode()
        if b"Custom flow:" in line else line
        for line in logs[representation].split(b"\n")
    )
    _stub_chip_tool(monkeypatch, qr, code, logs["qr"], logs["manual"])
    result = verification.verify_pair(qr, code, chip_tool="chip-tool")
    assert not result.passed
    assert result.chip_tool_status == "fail"
    assert result.chip_tool_reason == "chip-tool commissioning flow disagreed"


@pytest.mark.parametrize("flow,qr,code", VECTORS)
def test_chip_tool_flow_fields_remain_optional(monkeypatch, flow, qr, code):
    logs = [
        b"\n".join(line for line in log.split(b"\n") if b"Custom flow:" not in line)
        for log in (QR_LOGS[flow], MANUAL_LOGS[flow])
    ]
    _stub_chip_tool(monkeypatch, qr, code, *logs)
    result = verification.verify_pair(qr, code, chip_tool="chip-tool")
    assert result.passed
    assert result.chip_tool_status == "pass"


@pytest.mark.parametrize("flow,qr,code", VECTORS)
def test_local_verification_preserves_qr_flow_and_manual_code(monkeypatch, flow, qr, code):
    def unexpected_tool_call(args):
        pytest.fail("local verification must not invoke chip-tool")

    monkeypatch.setattr(verification, "_bounded_run", unexpected_tool_call)
    result = verification.verify_pair(qr, code)
    assert result.passed
    assert result.chip_tool_status == "not-requested"
    assert result.commissioning_flow == flow
    assert result.qr_setup_pin == 20202021
    assert result.discriminator == 3840
    assert result.short_discriminator == 15
    assert result.expected_code_length == result.supplied_code_length == len(code)

    # Valid checksum, but wrong PIN/discriminator: not merely a syntax failure.
    wrong = verification.verify_pair(qr, "00204800002")
    assert not wrong.passed
    assert wrong.reason == "pairing code does not match QR"

    # Matching PIN/discriminator but wrong standard/nonstandard representation.
    wrong_length_code = VECTORS[1 if flow == 0 else 0][2]
    wrong = verification.verify_pair(qr, wrong_length_code)
    assert not wrong.passed
    assert wrong.reason == "pairing code does not match QR"
