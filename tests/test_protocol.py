"""KBeacon login and request framing."""

from __future__ import annotations

import hashlib
import json

import pytest

from custom_components.irabelle_waste_management.kbeacon.protocol import (
    DT_HEX,
    TAG_END,
    AuthSession,
    KBeaconAuthError,
    KBeaconCommandError,
    RequestState,
    encode_json,
    report_ack,
)

MAC = "BC:57:29:28:1E:6E"
PASSWORD = "0000000000000000"
MAC_REVERSED = bytes.fromhex(MAC.replace(":", ""))[::-1]


def md5(random: bytes, password: str = PASSWORD) -> bytes:
    return hashlib.md5(MAC_REVERSED + bytes([0xA9, 0xB1]) + random + password.encode()).digest()


def fold(digest: bytes) -> bytes:
    return bytes(digest[i] ^ digest[i + 8] for i in range(8))


def test_login() -> None:
    auth = AuthSession(MAC, PASSWORD, app_random=b"\x01\x02\x03\x04")
    assert auth.request(now=0x01020304) == b"\x13\x01\x01\x02\x03\x04\x01\x02\x03\x04"
    device_random = b"\xaa\xbb\xcc\xdd"
    reply = auth.handle(bytes([1]) + device_random + md5(b"\x01\x02\x03\x04"))
    assert reply == b"\x13\x02" + md5(device_random)
    assert auth.handle(bytes([2, 247])) is None
    assert auth.done and auth.mtu_payload == 244


def test_login_wrong_password() -> None:
    auth = AuthSession(MAC, "1234567890", app_random=b"\x01\x02\x03\x04")
    with pytest.raises(KBeaconAuthError):
        auth.handle(bytes([1]) + b"\xaa\xbb\xcc\xdd" + md5(b"\x01\x02\x03\x04"))


def test_login_short_mtu() -> None:
    auth = AuthSession(MAC, PASSWORD, app_random=b"\x09\x09\x09\x09")
    reply = auth.handle(bytes([11]) + b"\xaa\xbb\xcc\xdd" + fold(md5(b"\x09\x09\x09\x09")))
    assert reply == bytes([0x13, 12]) + fold(md5(b"\xaa\xbb\xcc\xdd"))


def test_password_length() -> None:
    with pytest.raises(ValueError):
        AuthSession(MAC, "130186")


def test_long_request_is_chunked() -> None:
    message = {"msg": "cfg", "name": "X" * 50}
    state = RequestState(encode_json(message), mtu_payload=20)
    first = state.first()
    assert first[0] == 0x20 and first[1:3] == b"\x00\x00" and len(first) == 20
    sent = bytearray(first[3:])
    while True:
        ack = bytes([0x20]) + len(sent).to_bytes(2, "big") + b"\x03\xe8\x00\x04"
        out = state.handle(ack)
        if not out:
            break
        sent += out[0][3:]
        if out[0][0] & 0xF == TAG_END:
            break
    assert bytes(sent) == encode_json(message)
    state.handle(bytes([0x20, 0, 0, 3, 0xE8, 0, 0]))
    assert state.finished and state.json() == {}


def test_report_is_reassembled() -> None:
    reply = json.dumps({"name": "Aaa123", "advObj": [{"slot": 0, "type": 5}]}).encode()
    state = RequestState(encode_json({"msg": "getPara", "type": 3}), 244)
    assert state.handle(bytes([0x20, 0, 0, 3, 0xE8, 0, 5]) + reply[:30]) == [report_ack(30)]
    assert state.handle(bytes([0x31]) + (30).to_bytes(2, "big") + reply[30:50]) == [report_ack(50)]
    state.handle(bytes([0x32]) + (50).to_bytes(2, "big") + reply[50:])
    assert state.finished and state.json()["advObj"][0]["type"] == 5


def test_out_of_order_report_is_rejected() -> None:
    state = RequestState(b"{}", 244)
    state.handle(bytes([0x30, 0, 0]) + b'{"a"')
    assert state.handle(bytes([0x31, 0, 9]) + b":1}") == [report_ack(4, cause=1)]
    assert not state.finished


def test_rejected_command() -> None:
    state = RequestState(b"{}", 244)
    with pytest.raises(KBeaconCommandError) as err:
        state.handle(bytes([0x20, 0, 0, 3, 0xE8, 0, 0x22]))
    assert err.value.cause == 0x22


def test_hex_exchange_collects_items_until_complete() -> None:
    state = RequestState(b"\x02\x02", 244, data_type=DT_HEX)
    state.handle(bytes([0x00, 0, 0, 3, 0xE8, 0, 5]))  # command received
    assert state.handle(bytes([0x53, 0, 0]) + b"first") == [report_ack(5, data_type=5)]
    assert not state.finished
    state.handle(bytes([0x50, 0, 0]) + b"sec")
    state.handle(bytes([0x52, 0, 3]) + b"ond")
    assert not state.finished
    state.handle(bytes([0x00, 0, 0, 3, 0xE8, 0, 6]))  # command complete
    assert state.finished and state.items == [b"first", b"second"]
