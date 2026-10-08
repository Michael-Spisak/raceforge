"""The phone side of rftx1 (spec 0007) in Python, over an in-memory "GATT" link — the laptop code is
tested against it; TrackScoutKit has the same tests on the Swift side."""

import asyncio
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field

from raceforge.capture.rftx import MAX_CHUNK, Messenger, Msg, auth_mac, chunk_body, tag


class MemChannel:
    """One end of an in-memory link; ``disconnect()`` breaks both ends."""

    def __init__(
        self,
        inbox: "asyncio.Queue[bytes | None]",
        outbox: "asyncio.Queue[bytes | None]",
        max_payload: int,
    ) -> None:
        self._in = inbox
        self._out = outbox
        self.max_payload = max_payload
        self.closed = False

    async def send(self, fragment: bytes) -> None:
        if self.closed:
            raise ConnectionError("closed")
        assert len(fragment) <= self.max_payload + 1, "fragment larger than the GATT value"
        await self._out.put(fragment)

    async def recv(self) -> bytes:
        item = await self._in.get()
        if item is None:
            self.closed = True
            raise ConnectionError("disconnected")
        return item

    def disconnect(self) -> None:
        self.closed = True
        self._in.put_nowait(None)
        self._out.put_nowait(None)


def link(max_payload: int = 180) -> tuple[MemChannel, MemChannel]:
    a: asyncio.Queue[bytes | None] = asyncio.Queue()
    b: asyncio.Queue[bytes | None] = asyncio.Queue()
    return MemChannel(a, b, max_payload), MemChannel(b, a, max_payload)


@dataclass
class FakePhone:
    key: str  # the pairing key the phone got from the QR code
    passes: dict[str, bytes]
    project: str = "Corridor 2nd floor"
    chunk: int = MAX_CHUNK
    drop_after_chunks: int | None = None
    corrupt_chunk: int | None = None
    offer_key: str | None = None  # tag passes with another laptop's key
    sent_bytes: int = 0
    delivered: list[str] = field(default_factory=list)

    def entries(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for pid, data in self.passes.items():
            sha = hashlib.sha256(data).hexdigest()
            out.append(
                {
                    "id": pid,
                    "size": len(data),
                    "sha256": sha,
                    "project": self.project,
                    "pass_type": "walkthrough",
                    "created_at": f"2026-10-08T10:00:0{len(out)}Z",
                    "tag": tag(self.offer_key or self.key, pid, sha),
                }
            )
        return out

    async def serve(self, ch: MemChannel) -> None:
        m = Messenger(ch, timeout_s=5)
        nonce = os.urandom(16)
        chunks = 0
        try:
            await m.send(Msg.CHALLENGE, nonce)
            auth = json.loads(await m.expect(Msg.AUTH))
            if not hmac.compare_digest(auth["mac"], auth_mac(self.key, nonce)):
                await m.send_json(Msg.OFFER, {"error": "unpaired"})
                ch.disconnect()
                return
            await m.send_json(Msg.OFFER, {"passes": self.entries()})
            while True:
                kind, body = await m.recv()
                req = json.loads(body)
                if kind is Msg.DONE:
                    self.delivered.append(req["id"])
                    continue
                data = self.passes[req["id"]]
                for off in range(req["offset"], len(data), self.chunk):
                    if self.drop_after_chunks is not None and chunks >= self.drop_after_chunks:
                        self.drop_after_chunks = None  # only once
                        ch.disconnect()
                        return
                    piece = data[off : off + self.chunk]
                    body = chunk_body(off, piece)
                    if self.corrupt_chunk == chunks:
                        body = body[:-1] + bytes([body[-1] ^ 0xFF])
                    await m.send(Msg.CHUNK, body)
                    self.sent_bytes += len(piece)
                    chunks += 1
        except Exception:  # the laptop hung up: that is how a session ends
            return
