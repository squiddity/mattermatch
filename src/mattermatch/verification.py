"""Semantic Matter QR/manual pairing-code verification."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import signal
import subprocess
import threading
import time
from typing import Any, Sequence

from .matter import MalformedMatter, NonMatter, SetupPayload, derive_manual_code, normalize_manual_code, parse_matter_payload

MAX_CHIP_TOOL_OUTPUT = 64 * 1024
CHIP_TOOL_TIMEOUT_SECONDS = 5.0
_CHIP_TOOL_READ_CHUNK = 4096
_CHIP_TOOL_SHUTDOWN_GRACE_SECONDS = 0.25


@dataclass(frozen=True)
class VerificationResult:
    """Bounded fields suitable for either human or JSON output."""

    passed: bool
    reason: str
    qr_setup_pin: int | None = None
    discriminator: int | None = None
    short_discriminator: int | None = None
    commissioning_flow: int | None = None
    expected_code_length: int | None = None
    supplied_code_length: int | None = None
    payload_count: int | None = None
    chip_tool_status: str = "not-requested"
    chip_tool_reason: str | None = None

    def as_json(self) -> dict[str, object]:
        result = "PASS" if self.passed else "FAIL"
        return {
            "result": result,
            "status": result,
            "reason": self.reason,
            "qr_setup_pin": self.qr_setup_pin,
            "discriminator": self.discriminator,
            "short_discriminator": self.short_discriminator,
            "short_discriminator_relation": (
                f"(discriminator >> 8) & 0xf = {self.short_discriminator}"
                if self.short_discriminator is not None else None
            ),
            "commissioning_flow": self.commissioning_flow,
            "expected_code_length": self.expected_code_length,
            "supplied_code_length": self.supplied_code_length,
            "payload_count": self.payload_count,
            "chip_tool_status": self.chip_tool_status,
            "chip_tool_reason": self.chip_tool_reason,
        }


_ANSI_RE = re.compile(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])"
)


def _clean_tool_output(data: bytes | str) -> str:
    """Decode bounded bytes and remove terminal/control formatting."""
    if isinstance(data, bytes):
        text = data.decode("utf-8", "replace")
    else:
        text = str(data)
    text = _ANSI_RE.sub("", text)
    # Keep line structure and tabs for human/logger output, but never retain
    # control bytes in a parser input or diagnostic string.
    return "".join(char for char in text if char in "\n\r\t" or ord(char) >= 0x20 and ord(char) != 0x7F)


def _label_pattern(name: str) -> str:
    # Accept camelCase, snake_case, and human labels with arbitrary spacing.
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.replace("_", " ").replace("-", " "))
    words = re.findall(r"[A-Za-z0-9]+", separated)
    return r"\s*".join(re.escape(word) for word in words)


def _field(text: str, *names: str) -> int | None:
    """Parse a small integer from JSON-ish or logger-prefixed tool output."""
    text = _clean_tool_output(text)
    if not names:
        return None
    alternatives = "|".join(_label_pattern(name) for name in names)
    # The bounded line prefix allows forms such as ``I: ... Long
    # discriminator: 128`` as well as JSON keys after a comma or brace.
    match = re.search(
        r"(?im)(?:^|[,{\[\n\r])[^\n\r]{0,300}?(?:\b(?:" + alternatives + r")\b)"
        r"\s*[\"']?\s*(?:=|:)\s*[\"']?"
        r"(0[xX][0-9a-fA-F]+|[0-9]+)",
        text,
    )
    if match is None:
        return None
    value = match.group(1)
    try:
        return int(value[2:], 16) if value.lower().startswith("0x") else int(value, 10)
    except ValueError:
        return None


def _popen_options() -> dict[str, object]:
    """Return isolation options supported by the current platform."""
    options: dict[str, object] = {}
    if os.name == "posix":
        # The child becomes a new session/process-group leader.  This lets a
        # timeout or output overflow terminate descendants that inherited our
        # pipes instead of leaving reader threads blocked indefinitely.
        options["start_new_session"] = True
    elif os.name == "nt":
        # Python exposes group creation on Windows, although it has no direct
        # cross-version equivalent of POSIX killpg.  process.kill remains the
        # safe fallback when group control is unavailable.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            options["creationflags"] = flags
    return options


def _stop_process(process: Any) -> None:
    """Force-stop a process and, where supported, its isolated process group."""
    pid = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except (AttributeError, OSError, ProcessLookupError, ValueError):
            # The process may have exited, or a test/fallback process may not
            # expose a usable group.  Killing the direct child is still safe.
            pass
    try:
        process.kill()
    except (AttributeError, OSError, ProcessLookupError):
        pass


def _close_pipe(stream: Any) -> None:
    try:
        stream.close()
    except (AttributeError, OSError):
        pass


def _bounded_run(args: Sequence[str]) -> tuple[str, bytes, str | None]:
    """Run one tool command while retaining at most the configured bytes.

    Two reader threads continuously drain stdout and stderr, preventing a
    child blocked on either pipe from deadlocking the parent.  They append to
    one shared capped buffer rather than first capturing unbounded output in
    ``communicate()``.  Overflow and timeout kill the process before return.
    """
    base_options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    isolation_options = _popen_options()
    try:
        process = subprocess.Popen(list(args), **base_options, **isolation_options)
    except (TypeError, ValueError):
        # A nonstandard platform/runtime may reject the isolation keyword.
        # Retry once without it; the controller remains bounded and does not
        # join blocked readers on this less-safe fallback path.
        if not isolation_options:
            return "unavailable", b"", "chip-tool could not be started"
        try:
            process = subprocess.Popen(list(args), **base_options)
        except (OSError, TypeError, ValueError):
            return "unavailable", b"", "chip-tool could not be started"
    except OSError:
        if not isolation_options:
            return "unavailable", b"", "chip-tool could not be started"
        # Some POSIX-like/Windows runtimes reject an isolation option with an
        # OSError rather than TypeError.  Retry once with the direct-process
        # fallback; a missing executable is still reported as unavailable.
        try:
            process = subprocess.Popen(list(args), **base_options)
        except (OSError, TypeError, ValueError):
            return "unavailable", b"", "chip-tool could not be started"

    streams = (getattr(process, "stdout", None), getattr(process, "stderr", None))
    if streams[0] is None or streams[1] is None:
        _stop_process(process)
        for stream in streams:
            _close_pipe(stream)
        return "unavailable", b"", "chip-tool pipes were unavailable"

    output = bytearray()
    state_lock = threading.Lock()
    failure: list[str | None] = [None]
    finished = [threading.Event(), threading.Event()]

    def abort(reason: str) -> None:
        should_stop = False
        with state_lock:
            if failure[0] is None:
                failure[0] = reason
                should_stop = True
        if should_stop:
            # Never synchronously close a pipe from the controller: a reader
            # may be blocked in the OS read because a descendant inherited the
            # descriptor.  Group termination normally produces EOF; the
            # daemon readers are deliberately not joined on the fallback path.
            _stop_process(process)

    def reader(index: int, stream: Any) -> None:
        # Buffered pipe readers should use read1 when available: read(n) can
        # wait for n bytes or EOF even though a logger already emitted a small
        # line and a descendant still holds the descriptor open.
        read_chunk = getattr(stream, "read1", None)
        if not callable(read_chunk):
            read_chunk = stream.read
        try:
            while True:
                # Reading only one byte beyond the remaining budget lets us
                # detect overflow without retaining an unbounded pipe chunk.
                with state_lock:
                    read_size = min(
                        _CHIP_TOOL_READ_CHUNK,
                        max(1, MAX_CHIP_TOOL_OUTPUT - len(output) + 1),
                    )
                chunk = read_chunk(read_size)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", "replace")
                if not isinstance(chunk, bytes):
                    chunk = bytes(chunk)
                with state_lock:
                    if failure[0] is not None:
                        continue
                    remaining = MAX_CHIP_TOOL_OUTPUT - len(output)
                    if len(chunk) > remaining:
                        if remaining > 0:
                            output.extend(chunk[:remaining])
                        overflow = True
                    else:
                        output.extend(chunk)
                        overflow = False
                if overflow:
                    abort("overflow")
                    break
        except (AttributeError, OSError, ValueError, TypeError):
            # A forced pipe close during timeout/overflow is expected.
            pass
        finally:
            finished[index].set()

    threads = [
        threading.Thread(target=reader, args=(index, stream), daemon=True)
        for index, stream in enumerate(streams)
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + CHIP_TOOL_TIMEOUT_SECONDS
    while not all(event.is_set() for event in finished):
        with state_lock:
            current_failure = failure[0]
        if current_failure is not None:
            # Overflow kills immediately; do not make a broken pipe wait for
            # the full command timeout if a descendant retains a pipe handle.
            grace_deadline = time.monotonic() + _CHIP_TOOL_SHUTDOWN_GRACE_SECONDS
            while time.monotonic() < grace_deadline and not all(event.is_set() for event in finished):
                time.sleep(0.01)
            break
        if time.monotonic() >= deadline:
            abort("timeout")
            # Group termination wakes readers on normal OS pipes.  Do not
            # wait indefinitely on a hostile executable or descendant.
            grace_deadline = time.monotonic() + _CHIP_TOOL_SHUTDOWN_GRACE_SECONDS
            while time.monotonic() < grace_deadline and not all(event.is_set() for event in finished):
                time.sleep(0.01)
            break
        time.sleep(0.005)

    if failure[0] is not None:
        try:
            process.wait(timeout=_CHIP_TOOL_SHUTDOWN_GRACE_SECONDS)
        except (AttributeError, OSError, subprocess.TimeoutExpired, TypeError):
            _stop_process(process)
        reason = failure[0]
        return ("timeout" if reason == "timeout" else "fail"), bytes(output), (
            "chip-tool timed out" if reason == "timeout" else "chip-tool output exceeded safety limit"
        )

    try:
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (AttributeError, OSError, subprocess.TimeoutExpired, TypeError):
        _stop_process(process)
        return "timeout", bytes(output), "chip-tool timed out"
    if returncode != 0:
        return "fail", bytes(output), "chip-tool rejected the payload"
    return "pass", bytes(output), None


_QR_PASSCODE_FIELDS = (
    "Passcode", "passcode", "setup PIN code", "setup PIN", "setup_pin",
    "setUpPINCode", "setupPinCode", "setup code",
)
_MANUAL_PASSCODE_FIELDS = _QR_PASSCODE_FIELDS + ("pairing code", "manual pairing code")


def chip_tool_cross_check(
    path: str,
    qr: str,
    payload: SetupPayload,
    pairing_code: str | None = None,
) -> tuple[str, str | None]:
    """Cross-check both QR and manual-code parsers in an installed chip-tool.

    The two invocations deliberately use separate argument arrays.  Their
    outputs are independently compared with the local parser and with each
    other (setup PIN and long/short discriminator relationship).
    """
    normalized_code = pairing_code
    if normalized_code is None:
        normalized_code = derive_manual_code(payload)
    try:
        normalized_code = normalize_manual_code(normalized_code)
    except ValueError:
        return "fail", "pairing code is invalid"

    qr_status, qr_bytes, qr_reason = _bounded_run(
        [str(path), "payload", "parse-setup-payload", qr]
    )
    # chip-tool's parse-setup-payload command accepts both MT: QR payloads
    # and decimal manual pairing codes.
    manual_status, manual_bytes, manual_reason = _bounded_run(
        [str(path), "payload", "parse-setup-payload", normalized_code]
    )
    # Invoke both commands even when the first rejects, so a tool cannot make
    # the manual check conditional on QR parsing and tests can audit both args.
    if qr_status != "pass":
        return qr_status, qr_reason
    if manual_status != "pass":
        return manual_status, manual_reason

    qr_output = _clean_tool_output(qr_bytes)
    manual_output = _clean_tool_output(manual_bytes)
    qr_fields = {
        "discriminator": _field(qr_output, "long discriminator", "longDiscriminator", "discriminator"),
        "setup_pin": _field(qr_output, *_QR_PASSCODE_FIELDS),
        "commissioning_flow": _field(qr_output, "commissioning flow", "commissioningFlow", "flow"),
        "vendor_id": _field(qr_output, "vendor ID", "vendorID", "vendor_id"),
        "product_id": _field(qr_output, "product ID", "productID", "product_id"),
    }
    manual_fields = {
        "short_discriminator": _field(manual_output, "short discriminator", "shortDiscriminator"),
        "setup_pin": _field(manual_output, *_MANUAL_PASSCODE_FIELDS),
        "commissioning_flow": _field(manual_output, "commissioning flow", "commissioningFlow", "flow"),
        "vendor_id": _field(manual_output, "vendor ID", "vendorID", "vendor_id"),
        "product_id": _field(manual_output, "product ID", "productID", "product_id"),
    }
    if qr_fields["discriminator"] is None or qr_fields["setup_pin"] is None:
        return "fail", "chip-tool QR output omitted required fields"
    if manual_fields["short_discriminator"] is None or manual_fields["setup_pin"] is None:
        return "fail", "chip-tool manual-code output omitted required fields"

    if (
        qr_fields["discriminator"] != payload.discriminator
        or qr_fields["setup_pin"] != payload.setup_pin
    ):
        return "fail", "chip-tool QR fields disagreed"
    if (
        manual_fields["short_discriminator"] != payload.short_discriminator
        or manual_fields["setup_pin"] != payload.setup_pin
    ):
        return "fail", "chip-tool manual-code fields disagreed"
    if manual_fields["short_discriminator"] != ((qr_fields["discriminator"] >> 8) & 0xF):
        return "fail", "chip-tool discriminator relationship disagreed"

    # QR preserves all three flows, but manual codes encode only standard vs
    # nonstandard (the VID/PID-present bit). CHIP parses either nonstandard
    # flow as Custom (2), including a QR with UserActionRequired (1).
    # Flow is not printed by every chip-tool release; check it when present.
    manual_flow = 0 if payload.commissioning_flow == 0 else 2
    for fields, expected_flow in (
        (qr_fields, payload.commissioning_flow),
        (manual_fields, manual_flow),
    ):
        if fields["commissioning_flow"] is not None and fields["commissioning_flow"] != expected_flow:
            return "fail", "chip-tool commissioning flow disagreed"
    if payload.commissioning_flow != 0:
        for fields in (qr_fields, manual_fields):
            if fields["vendor_id"] is None or fields["product_id"] is None:
                return "fail", "chip-tool output omitted vendor or product"
            if fields["vendor_id"] != payload.vendor_id or fields["product_id"] != payload.product_id:
                return "fail", "chip-tool vendor or product disagreed"
    return "pass", None


def verify_pair(qr: str, pairing_code: str, *, chip_tool: str | None = None) -> VerificationResult:
    """Parse a complete QR and compare a normalized, Verhoeff-valid code."""
    supplied: str | None = None
    supplied_error: str | None = None
    try:
        supplied = normalize_manual_code(pairing_code)
    except ValueError as exc:
        supplied_error = str(exc)

    try:
        payloads = parse_matter_payload(qr)
    except NonMatter:
        return VerificationResult(False, "QR is not a Matter payload", supplied_code_length=len(supplied or pairing_code))
    except (MalformedMatter, ValueError):
        return VerificationResult(False, "QR payload is malformed", supplied_code_length=len(supplied or pairing_code))

    # A single manual code cannot unambiguously identify a concatenated QR.
    # Still parsing all chunks above is intentional: malformed trailing chunks
    # never get hidden by a valid first chunk.
    first = payloads[0]
    # Derive every logical code before applying the one-code concatenation
    # policy.  This keeps validation atomic and guarantees no trailing chunk
    # is treated as merely decorative.
    derived_codes = [derive_manual_code(payload) for payload in payloads]
    common = {
        "qr_setup_pin": first.setup_pin,
        "discriminator": first.discriminator,
        "short_discriminator": first.short_discriminator,
        "commissioning_flow": first.commissioning_flow,
        "expected_code_length": 11 if first.commissioning_flow == 0 else 21,
        "supplied_code_length": len(supplied or pairing_code),
        "payload_count": len(payloads),
    }
    if len(payloads) != 1:
        return VerificationResult(False, "concatenated QR requires one logical payload", **common)
    if supplied_error is not None or supplied is None:
        return VerificationResult(False, "pairing code is invalid", **common)

    expected = derived_codes[0]
    chip_status = "not-requested"
    chip_reason: str | None = None
    if chip_tool is not None:
        chip_status, chip_reason = chip_tool_cross_check(chip_tool, qr.strip(), first, supplied)
    passed = supplied == expected and chip_status in ("not-requested", "pass")
    if supplied != expected:
        reason = "pairing code does not match QR"
    elif chip_status not in ("not-requested", "pass"):
        reason = chip_reason or "chip-tool cross-check failed"
    else:
        reason = "pairing code matches QR"
    return VerificationResult(passed, reason, chip_tool_status=chip_status, chip_tool_reason=chip_reason, **common)
