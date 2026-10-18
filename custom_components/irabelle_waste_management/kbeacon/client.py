"""Async KBeacon configuration client on top of a connected BleakClient."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak import BleakClient

from .protocol import (
    CFG_ADV,
    CFG_COMMON,
    CFG_SENSOR,
    CFG_TRIGGER,
    DT_AUTH,
    DT_HEX,
    NOTIFY_CHAR_UUID,
    WRITE_CHAR_UUID,
    AuthSession,
    KBeaconError,
    RequestState,
    encode_json,
)

_LOGGER = logging.getLogger(__name__)

AUTH_TIMEOUT = 15.0
REQUEST_TIMEOUT = 15.0


class KBeaconClient:
    """Speak the KBeacon protocol over an already connected BleakClient."""

    def __init__(self, client: BleakClient, mac: str) -> None:
        """Wrap a connected client; call :meth:`login` before anything else."""
        self._client = client
        self._mac = mac
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._mtu_payload = 20
        self._notifying = False

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    async def _write(self, pdu: bytes) -> None:
        _LOGGER.debug("%s TX %s", self._mac, pdu.hex())
        await self._client.write_gatt_char(WRITE_CHAR_UUID, pdu, response=True)

    async def _next(self, timeout: float) -> bytes:
        pdu = await asyncio.wait_for(self._queue.get(), timeout)
        _LOGGER.debug("%s RX %s", self._mac, pdu.hex())
        return pdu

    async def login(self, password: str) -> None:
        """Subscribe to notifications and authenticate."""
        if not self._notifying:
            await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
            self._notifying = True
        auth = AuthSession(mac=self._mac, password=password)
        await self._write(auth.request())
        async with asyncio.timeout(AUTH_TIMEOUT):
            while not auth.done:
                pdu = await self._queue.get()
                _LOGGER.debug("%s RX %s", self._mac, pdu.hex())
                if pdu[0] >> 4 != DT_AUTH:
                    continue
                if (reply := auth.handle(pdu[1:])) is not None:
                    await self._write(reply)
        self._mtu_payload = auth.mtu_payload
        _LOGGER.debug("%s logged in, payload MTU %s", self._mac, self._mtu_payload)

    async def request(
        self, message: dict[str, Any], timeout: float = REQUEST_TIMEOUT
    ) -> dict[str, Any]:
        """Send a JSON message and return the beacon's JSON reply ({} if none)."""
        state = await self._exchange(RequestState(encode_json(message), self._mtu_payload), timeout)
        return state.json()

    async def hex_request(self, payload: bytes, timeout: float = 30.0) -> list[bytes]:
        """Send a binary (sensor) message and return every reply item."""
        state = await self._exchange(
            RequestState(payload, self._mtu_payload, data_type=DT_HEX), timeout
        )
        return state.items or ([state.response] if state.response else [])

    async def _exchange(self, state: RequestState, timeout: float) -> RequestState:
        # Drop anything left over from a previous exchange.
        while not self._queue.empty():
            self._queue.get_nowait()
        await self._write(state.first())
        async with asyncio.timeout(timeout):
            while not state.finished:
                pdu = await self._queue.get()
                _LOGGER.debug("%s RX %s", self._mac, pdu.hex())
                for out in state.handle(pdu):
                    await self._write(out)
        return state

    # -- high level ---------------------------------------------------------

    async def read_config(self, sensors: bool = True) -> dict[str, Any]:
        """Read common, slot, trigger (and sensor) settings into one dict.

        Like the SDK, read at most two kinds per message to stay under the
        beacon's 4 KiB reply buffer.
        """
        merged: dict[str, Any] = {}
        rounds = [CFG_COMMON | CFG_ADV, CFG_TRIGGER]
        if sensors:
            rounds.append(CFG_SENSOR)
        for kinds in rounds:
            try:
                reply = await self.request({"msg": "getPara", "type": kinds})
            except KBeaconError as err:
                if kinds in (CFG_TRIGGER, CFG_SENSOR):
                    # Models without triggers/sensors reject the request.
                    _LOGGER.debug("%s: reading type %s failed: %s", self._mac, kinds, err)
                    continue
                raise
            for key, value in reply.items():
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
                else:
                    merged[key] = value
        merged.pop("msg", None)
        return merged

    async def write_config(self, config: dict[str, Any]) -> None:
        """Write settings: common fields plus advObj / trObj / srObj lists."""
        await self.request({"msg": "cfg", **config})

    async def ring(self, ring_ms: int = 10000, ring_type: int = 2) -> None:
        """Flash the LED (ring_type 2) and/or beep (1) to identify the beacon."""
        await self.request(
            {"msg": "ring", "ringTime": ring_ms, "ringType": ring_type, "ledOn": 200, "ledOff": 800}
        )

    async def reset(self) -> None:
        """Factory-reset the configuration."""
        await self.request({"msg": "admin", "stype": "reset"})

    async def power_off(self) -> None:
        """Power the beacon off (press the button to turn it back on)."""
        await self.request({"msg": "admin", "stype": "pwroff"})
