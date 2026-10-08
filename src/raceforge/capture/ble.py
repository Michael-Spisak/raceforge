"""Bluetooth LE channel to TrackScout (spec 0007 scope 9) via bleak (optional extra ``transfer``).

The laptop is the GATT central: it finds the phone by the TrackScout service UUID, subscribes to the
phone's notify characteristic and writes requests to the other one. The rftx1 protocol on top lives
in ``raceforge.capture.rftx``.
"""

import asyncio
from typing import Any

from raceforge.capture.rftx import RX_UUID, SERVICE_UUID, TX_UUID


class BleChannel:  # pragma: no cover - needs a real phone (device checklist, part B)
    """``rftx.Channel`` over one GATT connection. Use ``async with await BleChannel.connect():``."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.max_payload = max(20, int(getattr(client, "mtu_size", 23)) - 3 - 1)

    @classmethod
    async def connect(cls, timeout_s: float = 15.0) -> "BleChannel":
        try:
            from bleak import BleakClient, BleakScanner  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise RuntimeError(
                "Bluetooth transfer needs the optional 'transfer' extra (bleak)"
            ) from exc

        def has_service(_device: Any, adv: Any) -> bool:
            return SERVICE_UUID in [u.lower() for u in adv.service_uuids]

        device: Any = await BleakScanner.find_device_by_filter(has_service, timeout=timeout_s)
        if device is None:
            raise ConnectionError(
                "no TrackScout phone found — open TrackScout and pick 'Bluetooth'"
            )
        holder: list[BleChannel] = []

        def lost(_client: Any) -> None:
            if holder:
                holder[0].queue.put_nowait(None)

        client: Any = BleakClient(device, disconnected_callback=lost)
        await client.connect()
        channel = cls(client)
        holder.append(channel)
        await client.start_notify(TX_UUID, lambda _c, data: channel.queue.put_nowait(bytes(data)))
        return channel

    async def send(self, fragment: bytes) -> None:
        await self.client.write_gatt_char(RX_UUID, fragment, response=True)

    async def recv(self) -> bytes:
        item = await self.queue.get()
        if item is None:
            raise ConnectionError("phone disconnected")
        return item

    async def close(self) -> None:
        await self.client.disconnect()

    async def __aenter__(self) -> "BleChannel":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
