"""KKM KBeacon Pro configuration protocol (pure, no I/O).

Ported from KKM's Android SDK (github.com/kkmhogen/android_kbeaconlib2,
``KBeacon.java`` and ``KBAuthHandler.java``). Everything travels over GATT
service 0xFEA0: the app writes to 0xFEA1 and the beacon answers with
notifications on 0xFEA2.

Every PDU starts with one header byte ``(data_type << 4) | frame_tag``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import hashlib
import json
import secrets
import time
from typing import Any

SERVICE_UUID = "0000fea0-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000fea1-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000fea2-0000-1000-8000-00805f9b34fb"
INDICATE_CHAR_UUID = "0000fea3-0000-1000-8000-00805f9b34fb"

DEFAULT_PASSWORD = "0000000000000000"
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 16

# Data types (upper nibble of the header byte)
DT_HEX = 0  # app -> beacon hex data / beacon -> app hex ack
DT_AUTH = 1
DT_JSON = 2  # app -> beacon JSON data / beacon -> app JSON ack
DT_REPORT = 3  # beacon -> app JSON data report / app -> beacon report ack
DT_HEX_REPORT = 5

# Frame tags (lower nibble)
TAG_START = 0
TAG_MIDDLE = 1
TAG_END = 2
TAG_SINGLE = 3

PDU_HEAD_LEN = 3  # header + 2-byte offset
ACK_HEAD_LEN = 6  # offset(2) + window(2) + cause(2)
DEFAULT_MTU_PAYLOAD = 20  # BLE 4 MTU 23 - 3
MAX_BUFFER = 4096
REPORT_WINDOW = 1000

# Auth
_AUTH_PHASE1 = 0x01
_AUTH_PHASE2 = 0x02
_AUTH_SHORT_PHASE1 = 11
_AUTH_SHORT_PHASE2 = 12
_AUTH_FAIL = 0xF1
_AUTH_FACTOR = bytes([0xA9, 0xB1])
_MTU_SIZE_HEAD = 3

# Config read sub types (bitmask for {"msg": "getPara", "type": n})
CFG_COMMON = 0x1
CFG_ADV = 0x2
CFG_TRIGGER = 0x4
CFG_SENSOR = 0x8


class AckCause(IntEnum):
    """Cause codes in the beacon's acknowledgements."""

    SUCCESS = 0x0
    EXPECT_NEXT = 0x4
    CMD_RECEIVED = 0x5
    CMD_COMPLETE = 0x6
    CMD_INCOMPLETE = 0x7


class KBeaconError(Exception):
    """A protocol-level failure."""


class KBeaconAuthError(KBeaconError):
    """Wrong password (or the beacon refused the login)."""


class KBeaconCommandError(KBeaconError):
    """The beacon rejected a command or configuration."""

    def __init__(self, cause: int) -> None:
        """Keep the beacon's cause code."""
        super().__init__(f"Beacon rejected the request (cause {cause})")
        self.cause = cause


def header(data_type: int, tag: int) -> int:
    """Return the PDU header byte."""
    return ((data_type << 4) + tag) & 0xFF


def validate_password(password: str) -> None:
    """Raise ValueError unless the password has an accepted length."""
    if not PASSWORD_MIN_LEN <= len(password) <= PASSWORD_MAX_LEN:
        raise ValueError(
            f"Password must be {PASSWORD_MIN_LEN}-{PASSWORD_MAX_LEN} characters"
        )


# -- authentication ------------------------------------------------------------


@dataclass
class AuthSession:
    """MD5 challenge/response login (KBAuthHandler)."""

    mac: str
    password: str
    sync_utc: bool = True
    app_random: bytes = field(default_factory=lambda: secrets.token_bytes(4))
    mtu_payload: int = DEFAULT_MTU_PAYLOAD
    done: bool = False

    def __post_init__(self) -> None:
        """Validate inputs."""
        validate_password(self.password)
        mac = bytes.fromhex(self.mac.replace(":", "").replace("-", ""))
        if len(mac) != 6:
            raise ValueError("MAC address must have 6 bytes")
        # The SDK hashes the MAC in reverse byte order.
        self._mac_reversed = mac[::-1]

    def request(self, now: float | None = None) -> bytes:
        """First write: phase-1 request with the app's random (and UTC time)."""
        body = bytes([header(DT_AUTH, TAG_SINGLE), _AUTH_PHASE1]) + self.app_random
        if self.sync_utc:
            utc = int(time.time() if now is None else now) & 0xFFFFFFFF
            body += utc.to_bytes(4, "big")
        return body

    def _md5(self, random: bytes) -> bytes:
        return hashlib.md5(
            self._mac_reversed + _AUTH_FACTOR + random + self.password.encode()
        ).digest()

    @staticmethod
    def _fold(digest: bytes) -> bytes:
        return bytes(digest[i] ^ digest[i + 8] for i in range(8))

    def handle(self, body: bytes) -> bytes | None:
        """Handle an auth notification body; return the next write, if any.

        Raises KBeaconAuthError when the beacon's proof does not match (wrong
        password) or the beacon reports a failure.
        """
        if not body:
            raise KBeaconAuthError("Empty auth response")
        code = body[0]
        if code in (_AUTH_PHASE1, _AUTH_SHORT_PHASE1):
            short = code == _AUTH_SHORT_PHASE1
            data = body[1:]
            proof_len = 8 if short else 16
            if len(data) < 4 + proof_len:
                raise KBeaconAuthError("Short auth response")
            dev_random, dev_proof = data[:4], data[4 : 4 + proof_len]
            expected = self._md5(self.app_random)
            if short:
                expected = self._fold(expected)
            if expected != dev_proof:
                raise KBeaconAuthError("Wrong password")
            answer = self._md5(dev_random)
            if short:
                return bytes([header(DT_AUTH, TAG_SINGLE), _AUTH_SHORT_PHASE2]) + self._fold(answer)
            return bytes([header(DT_AUTH, TAG_SINGLE), _AUTH_PHASE2]) + answer
        if code == _AUTH_PHASE2:
            if len(body) >= 2:
                self.mtu_payload = max(body[1] - _MTU_SIZE_HEAD, DEFAULT_MTU_PAYLOAD)
            self.done = True
            return None
        if code == _AUTH_FAIL:
            raise KBeaconAuthError("Beacon refused the login")
        raise KBeaconAuthError(f"Unexpected auth code {code}")


# -- request framing -------------------------------------------------------------


def encode_json(message: dict[str, Any]) -> bytes:
    """Serialize a message the way the SDK does (compact JSON)."""
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()


def down_frame(payload: bytes, offset: int, mtu_payload: int, data_type: int = DT_JSON) -> bytes:
    """Return the PDU carrying ``payload`` from ``offset`` (sendNextCfgData)."""
    max_data = mtu_payload - PDU_HEAD_LEN
    if max_data <= 0:
        raise KBeaconError("MTU too small")
    if len(payload) <= max_data:
        tag, length = TAG_SINGLE, len(payload)
    elif offset == 0:
        tag, length = TAG_START, max_data
    elif offset + max_data < len(payload):
        tag, length = TAG_MIDDLE, max_data
    else:
        tag, length = TAG_END, len(payload) - offset
    return (
        bytes([header(data_type, tag)])
        + offset.to_bytes(2, "big")
        + payload[offset : offset + length]
    )


def report_ack(received: int, data_type: int = DT_REPORT, cause: int = 0) -> bytes:
    """Acknowledge ``received`` bytes of a data report (configSendDataRptAck)."""
    return (
        bytes([header(data_type, TAG_SINGLE)])
        + received.to_bytes(2, "big")
        + REPORT_WINDOW.to_bytes(2, "big")
        + cause.to_bytes(2, "big")
    )


@dataclass
class RequestState:
    """Drives one request/response exchange from the beacon's notifications.

    Feed every non-auth notification to :meth:`handle`; it returns the PDUs to
    write next. ``finished`` turns True when the exchange is complete and
    ``response`` then holds the reply bytes (possibly empty).
    """

    payload: bytes
    mtu_payload: int
    data_type: int = DT_JSON
    buffer: bytearray = field(default_factory=bytearray)
    finished: bool = False
    # Hex exchanges (sensor history) can return several report items; they
    # only end when the beacon acknowledges with CMD_COMPLETE (or SUCCESS).
    items: list[bytes] = field(default_factory=list)

    def first(self) -> bytes:
        """The first PDU to write."""
        return down_frame(self.payload, 0, self.mtu_payload, self.data_type)

    @property
    def response(self) -> bytes:
        """Reply bytes collected so far."""
        return bytes(self.buffer)

    def handle(self, pdu: bytes) -> list[bytes]:
        """Process one notification; return PDUs to write (may be empty)."""
        if len(pdu) < 2:
            return []
        data_type, tag, body = pdu[0] >> 4, pdu[0] & 0xF, pdu[1:]
        if data_type in (DT_JSON, DT_HEX):
            return self._handle_ack(data_type, body)
        if data_type in (DT_REPORT, DT_HEX_REPORT):
            return self._handle_report(data_type, tag, body)
        return []

    def _handle_ack(self, data_type: int, body: bytes) -> list[bytes]:
        if len(body) < ACK_HEAD_LEN:
            return []
        offset = int.from_bytes(body[0:2], "big")
        cause = int.from_bytes(body[4:6], "big")
        data = body[ACK_HEAD_LEN:]
        report_type = DT_REPORT if data_type == DT_JSON else DT_HEX_REPORT
        if cause == AckCause.EXPECT_NEXT:
            if offset < len(self.payload):
                return [down_frame(self.payload, offset, self.mtu_payload, self.data_type)]
            return []
        if cause == AckCause.CMD_RECEIVED:
            # The beacon accepted the request and will stream a report.
            if data:
                self.buffer[:] = data
                return [report_ack(len(self.buffer), report_type)]
            return []
        if cause == AckCause.SUCCESS:
            if data:
                self.buffer[:] = data
                if self.data_type == DT_HEX:
                    self.items.append(bytes(data))
            self.finished = True
            return []
        if cause == AckCause.CMD_COMPLETE:
            self.finished = True
            return []
        if cause == AckCause.CMD_INCOMPLETE:
            return []
        raise KBeaconCommandError(cause)

    def _handle_report(self, data_type: int, tag: int, body: bytes) -> list[bytes]:
        if len(body) < 2:
            return []
        offset = int.from_bytes(body[0:2], "big")
        payload = body[2:]
        if tag == TAG_START:
            self.buffer[:] = payload
            return [report_ack(len(self.buffer), data_type)]
        if tag in (TAG_MIDDLE, TAG_END):
            if offset != len(self.buffer) or len(self.buffer) + len(payload) > MAX_BUFFER:
                return [report_ack(len(self.buffer), data_type, cause=1)]
            self.buffer += payload
            ack = report_ack(len(self.buffer), data_type)
            if tag == TAG_END:
                self._complete_item(data_type)
            return [ack]
        if tag == TAG_SINGLE:
            self.buffer += payload
            ack = report_ack(len(self.buffer), data_type)
            self._complete_item(data_type)
            return [ack]
        return []

    def _complete_item(self, data_type: int) -> None:
        if data_type == DT_HEX_REPORT:
            # More items may follow; the exchange ends with CMD_COMPLETE.
            self.items.append(bytes(self.buffer))
            self.buffer.clear()
        else:
            self.finished = True

    def json(self) -> dict[str, Any]:
        """Decode the reply as JSON (empty dict when there is none)."""
        if not self.buffer:
            return {}
        try:
            return json.loads(self.buffer.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise KBeaconError(f"Invalid JSON reply: {err}") from err
