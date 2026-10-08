"""Phone → laptop transfer v1 ("rftx1", spec 0007): pairing tags, Bluetooth framing, laptop side.

The Bluetooth path splits *messages* (``u8 type`` + body) into GATT-sized *fragments* (``u8 flags``
+ payload, bit 0 = last fragment). The laptop proves that it knows the pairing key (HMAC over the
phone's nonce) before the phone offers anything; every chunk carries a CRC-32 and the whole file a
SHA-256.
"""

import asyncio
import hashlib
import hmac
import json
import struct
import zlib
from collections.abc import Callable
from enum import IntEnum
from typing import Any, Protocol

from raceforge.capture.inbox import Inbox, OfferedPass

SERVICE_UUID = "7e0f0001-5a1d-4c55-9b7e-52464f524745"
RX_UUID = "7e0f0002-5a1d-4c55-9b7e-52464f524745"  # laptop → phone (write with response)
TX_UUID = "7e0f0003-5a1d-4c55-9b7e-52464f524745"  # phone → laptop (notify)
MAX_CHUNK = 64 * 1024
_LAST = 0x01
_CHUNK_HEAD = struct.Struct("<QI")


class Msg(IntEnum):
    CHALLENGE = 1
    AUTH = 2
    OFFER = 3
    GET = 4
    CHUNK = 5
    DONE = 6
    ERROR = 7


class TransferError(Exception):
    """The phone rejected us, sent something invalid, or the connection broke."""


def tag(key: str, pass_id: str, sha256: str) -> str:
    """Pairing tag of an offered pass: only a laptop with the same key can verify it."""
    msg = f"rftx1|{pass_id}|{sha256}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def auth_mac(key: str, nonce: bytes) -> str:
    return hmac.new(key.encode(), b"rftx1-auth|" + nonce, hashlib.sha256).hexdigest()


def fragments(kind: Msg, body: bytes, max_payload: int) -> list[bytes]:
    """Split one message into GATT values of at most ``max_payload + 1`` bytes."""
    data = bytes([kind]) + body
    size = max(1, max_payload)
    parts = [data[i : i + size] for i in range(0, len(data), size)]
    return [bytes([_LAST if i == len(parts) - 1 else 0]) + p for i, p in enumerate(parts)]


def chunk_body(offset: int, data: bytes) -> bytes:
    return _CHUNK_HEAD.pack(offset, zlib.crc32(data)) + data


def parse_chunk(body: bytes) -> tuple[int, bytes]:
    if len(body) < _CHUNK_HEAD.size:
        raise TransferError("short chunk")
    offset, crc = _CHUNK_HEAD.unpack_from(body)
    data = body[_CHUNK_HEAD.size :]
    if zlib.crc32(data) != crc:
        raise TransferError(f"chunk at {offset}: CRC mismatch")
    return offset, data


class Channel(Protocol):
    """One GATT connection: ``send`` writes a fragment, ``recv`` returns the next notified fragment
    (raises ``ConnectionError`` when the link is gone)."""

    max_payload: int

    async def send(self, fragment: bytes) -> None: ...

    async def recv(self) -> bytes: ...


class Messenger:
    """Messages on top of a fragment channel."""

    def __init__(self, channel: Channel, timeout_s: float = 30.0) -> None:
        self.ch = channel
        self.timeout_s = timeout_s

    async def send(self, kind: Msg, body: bytes = b"") -> None:
        for f in fragments(kind, body, self.ch.max_payload):
            await self.ch.send(f)

    async def send_json(self, kind: Msg, obj: dict[str, Any]) -> None:
        await self.send(kind, json.dumps(obj).encode())

    async def recv(self) -> tuple[Msg, bytes]:
        buf = bytearray()
        while True:
            try:
                frag = await asyncio.wait_for(self.ch.recv(), self.timeout_s)
            except TimeoutError as exc:
                raise TransferError("phone stopped answering") from exc
            except ConnectionError as exc:
                raise TransferError(f"connection lost: {exc}") from exc
            if not frag:
                continue
            buf += frag[1:]
            if frag[0] & _LAST:
                break
        if not buf:
            raise TransferError("empty message")
        try:
            kind = Msg(buf[0])
        except ValueError as exc:
            raise TransferError(f"unknown message type {buf[0]}") from exc
        if kind is Msg.ERROR:
            raise TransferError(f"phone: {_json(bytes(buf[1:])).get('error', 'error')}")
        return kind, bytes(buf[1:])

    async def expect(self, kind: Msg) -> bytes:
        got, body = await self.recv()
        if got is not kind:
            raise TransferError(f"expected {kind.name}, got {got.name}")
        return body


def _json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except ValueError as exc:
        raise TransferError("invalid JSON from phone") from exc
    if not isinstance(value, dict):
        raise TransferError("invalid JSON from phone")
    return value  # pyright: ignore[reportUnknownVariableType]


async def pull(
    channel: Channel,
    key: str,
    laptop_name: str,
    inbox: Inbox,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Laptop side over Bluetooth: authenticate, take every offered pass (resuming ``.part`` files).

    Returns the ids of the passes now complete in the inbox. A broken link raises ``TransferError``;
    calling ``pull`` again on a new connection resumes where it stopped.
    """
    m = Messenger(channel)
    nonce = await m.expect(Msg.CHALLENGE)
    await m.send_json(Msg.AUTH, {"laptop": laptop_name, "mac": auth_mac(key, nonce)})
    offer = _json(await m.expect(Msg.OFFER))
    if "error" in offer:
        raise TransferError(f"phone: {offer['error']}")
    done: list[str] = []
    for raw in offer.get("passes", []):
        entry = OfferedPass.model_validate(raw)
        if not hmac.compare_digest(entry.tag, tag(key, entry.id, entry.sha256)):
            continue  # offered for another laptop
        offset = inbox.begin(entry, "bluetooth")
        if offset < entry.size:
            await m.send_json(Msg.GET, {"id": entry.id, "offset": offset})
            while offset < entry.size:
                at, data = parse_chunk(await m.expect(Msg.CHUNK))
                if at != offset or not data:
                    raise TransferError(f"{entry.id}: chunk at {at}, expected {offset}")
                inbox.append(entry.id, data)
                offset += len(data)
                if progress:
                    progress(entry.id, offset, entry.size)
        inbox.complete(entry.id)
        await m.send_json(Msg.DONE, {"id": entry.id, "sha256": entry.sha256})
        done.append(entry.id)
    return done
