import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from PIL import Image

from mattermatch.cli import EXIT_DIAGNOSTICS, EXIT_OK, EXIT_VERIFY_FAIL, main
from mattermatch.decoder import Detection, ZXingCppDecoder, merge_variant_detections
from mattermatch.matter import base38_encode, derive_manual_code, parse_matter_payload
import mattermatch.verification as verification

VALID = "MT:M5L90MP500K64J00000"


def _payload(*, vendor=1, product=1, flow=1, discriminator=0xF00, pin=12349876):
    values = [(0, 3), (vendor, 16), (product, 16), (flow, 2), (1, 8),
              (discriminator, 12), (pin, 27), (0, 4)]
    data = bytearray(11)
    offset = 0
    for value, width in values:
        for bit in range(width):
            if value & (1 << bit):
                data[(offset + bit) // 8] |= 1 << ((offset + bit) % 8)
        offset += width
    return "MT:" + base38_encode(data)


def test_verify_pair_golden_leading_zero_and_json(tmp_path):
    qr = _payload()
    code = derive_manual_code(parse_matter_payload(qr)[0])
    out, err = io.StringIO(), io.StringIO()
    assert main(
        ["verify-pair", "--json", "--qr", qr, "--pairing-code", code[:2] + "-" + code[2:]],
        stdout=out,
        stderr=err,
    ) == EXIT_OK
    record = json.loads(out.getvalue())
    assert record["result"] == "PASS"
    assert record["qr_setup_pin"] == 12349876
    assert record["discriminator"] == 0xF00
    assert record["short_discriminator"] == 0xF
    assert record["expected_code_length"] == 21
    assert err.getvalue() == ""


def test_verify_pair_chip_tool_argument_arrays_and_bounded_fields(monkeypatch):
    calls = []

    class FakeProcess:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))
            self.stdout = io.BytesIO(
                b'{"discriminator":128,"setUpPINCode":2048,"commissioningFlow":0}'
                if args[-1].startswith("MT:")
                else b'{"shortDiscriminator":0,"setUpPINCode":2048}'
            )
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    out, err = io.StringIO(), io.StringIO()
    assert main(
        ["verify-pair", "--chip-tool", "chip-tool", "--qr", VALID, "--pairing-code", "00204800002"],
        stdout=out,
        stderr=err,
    ) == EXIT_OK
    assert [call[0] for call in calls] == [
        ["chip-tool", "payload", "parse-setup-payload", VALID],
        ["chip-tool", "payload", "parse-setup-payload", "00204800002"],
    ]
    assert all(call[1]["shell"] is False for call in calls)
    assert "PASS" in out.getvalue()


def test_verify_pair_chip_tool_human_output_and_manual_mismatch(monkeypatch):
    calls = []

    class FakeProcess:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdout = io.BytesIO(
                b"\x1b[32mI: Passcode: 2048\x1b[0m\nLong discriminator: 128\n"
                if args[-1].startswith("MT:")
                else b"Passcode: 2048\nShort discriminator: 1\n"
            )
            self.stderr = io.BytesIO(b"logger: okay\\n")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    out, err = io.StringIO(), io.StringIO()
    assert main(
        ["verify-pair", "--chip-tool", "chip-tool", "--qr", VALID, "--pairing-code", "00204800002"],
        stdout=out,
        stderr=err,
    ) == EXIT_VERIFY_FAIL
    assert len(calls) == 2
    assert "manual-code" in err.getvalue()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups provide descendant cleanup")
def test_bounded_run_kills_pipe_holding_descendant_near_timeout(tmp_path, monkeypatch):
    child_pid = tmp_path / "child.pid"
    script = tmp_path / "retain-pipes.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "pid_path = pathlib.Path(sys.argv[1])\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pid_path.write_text(str(child.pid))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    timeout = 0.2
    monkeypatch.setattr(verification, "CHIP_TOOL_TIMEOUT_SECONDS", timeout)
    started = time.monotonic()
    status, output, reason = verification._bounded_run(
        [sys.executable, str(script), str(child_pid)]
    )
    elapsed = time.monotonic() - started
    assert status == "timeout"
    assert reason == "chip-tool timed out"
    assert b"ready" in output
    assert elapsed < timeout + 1.0
    child = int(child_pid.read_text(encoding="utf-8"))
    # A killed child can briefly remain a zombie while init reaps it, but must
    # not remain a live process after the isolated group was terminated.
    for _ in range(20):
        try:
            os.kill(child, 0)
        except OSError:
            break
        proc_stat = Path(f"/proc/{child}/stat")
        if proc_stat.exists() and proc_stat.read_text(encoding="utf-8").split()[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("pipe-holding descendant survived process-group termination")


def test_bounded_run_kills_on_live_output_overflow(tmp_path, monkeypatch):
    script = tmp_path / "overflow.py"
    script.write_text(
        "import sys, time\n"
        "sys.stdout.write('x' * 200000)\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verification, "MAX_CHIP_TOOL_OUTPUT", 1024)
    started = time.monotonic()
    status, output, reason = verification._bounded_run([sys.executable, str(script)])
    elapsed = time.monotonic() - started
    assert status == "fail"
    assert reason == "chip-tool output exceeded safety limit"
    assert len(output) <= 1024
    assert elapsed < 1.0


def test_verify_pair_chip_tool_rejection_still_attempts_both(monkeypatch):
    calls = []

    class RejectingProcess:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO(b"rejected")
            self.returncode = 1

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", RejectingProcess)
    result = main(
        ["verify-pair", "--chip-tool", "chip-tool", "--qr", VALID, "--pairing-code", "00204800002"],
        stdout=io.StringIO(), stderr=io.StringIO(),
    )
    assert result == EXIT_VERIFY_FAIL
    assert len(calls) == 2


def test_verify_pair_mismatch_and_concatenated_policy():
    out, err = io.StringIO(), io.StringIO()
    assert main(
        ["verify-pair", "--qr", VALID, "--pairing-code", "00204900003"],
        stdout=out,
        stderr=err,
    ) == EXIT_VERIFY_FAIL
    assert "does not match" in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert main(
        [
            "verify-pair", "--json", "--qr",
            VALID + "*M5L90U.D010K4J00000", "--pairing-code", "00204800002",
        ], stdout=out, stderr=err,
    ) == EXIT_VERIFY_FAIL
    assert json.loads(out.getvalue())["payload_count"] == 2
    assert "concatenated" in json.loads(out.getvalue())["reason"]


def test_retry_merge_preserves_same_payload_at_distinct_positions():
    first = Detection(VALID, [(1, 1), (11, 1), (11, 11), (1, 11)])
    repeat = Detection(VALID, [(2, 2), (12, 2), (12, 12), (2, 12)])
    other_position = Detection(VALID, [(50, 50), (60, 50), (60, 60), (50, 60)])
    merged = merge_variant_detections([[first], [repeat, other_position]])
    assert merged == [first, other_position]


def test_retry_does_not_stop_on_non_matter_native_count(tmp_path, monkeypatch):
    image = tmp_path / "input.png"
    Image.new("RGB", (12, 12), "white").save(image)
    decoder = ZXingCppDecoder()
    attempts = iter([
        [Detection("not Matter")],
        [Detection(VALID)],
        [],
        [],
    ])
    monkeypatch.setattr(decoder, "_decode_rgb", lambda rgb, max_results=None: next(attempts))
    detections = decoder.decode_with_preprocessing(image, expected_qr_count=1)
    assert [item.text for item in detections] == ["not Matter", VALID]


class RetryDecoder:
    def decode_with_preprocessing(self, path, *, expected_qr_count=None):
        return [Detection(VALID, [(2, 2), (10, 2), (10, 10), (2, 10)])]


def test_diagnostic_artifacts_are_safe_and_deterministic(tmp_path):
    image = tmp_path / "input.png"
    inventory = tmp_path / "inventory.csv"
    Image.new("RGB", (20, 20), "white").save(image)
    inventory.write_text("code,descriptor\n00204800002,lamp\n", encoding="utf-8")
    diagnostics = tmp_path / "artifacts"
    kwargs = [
        "--inventory", str(inventory), "--retry-preprocessing",
        "--diagnostics-dir", str(diagnostics), str(image),
    ]
    assert main(kwargs, decoder=RetryDecoder(), stdout=io.StringIO(), stderr=io.StringIO()) == EXIT_OK
    names = sorted(item.name for item in diagnostics.iterdir())
    assert names[0].endswith("-annotated.png")
    assert names[1].endswith("-contact.png")
    # Re-running unchanged input reuses identical bytes rather than changing
    # file names/content.
    assert main(kwargs, decoder=RetryDecoder(), stdout=io.StringIO(), stderr=io.StringIO()) == EXIT_OK


def test_diagnostic_artifact_failure_is_nonzero(tmp_path):
    inventory = tmp_path / "inventory.csv"
    inventory.write_text("code,descriptor\n", encoding="utf-8")
    err = io.StringIO()
    rc = main(
        ["--inventory", str(inventory), "--diagnostics-dir", str(tmp_path / "d"), "missing.png"],
        decoder=RetryDecoder(), stdout=io.StringIO(), stderr=err,
    )
    assert rc == EXIT_DIAGNOSTICS
    assert "artifacts could not be written" in err.getvalue()
