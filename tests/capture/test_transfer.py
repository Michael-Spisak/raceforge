"""Spec 0007 AC9: phone → laptop transfer: Bluetooth (in-memory link) and cable (fake folder)."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from raceforge.capture.inbox import Inbox, OfferedPass
from raceforge.capture.rftx import Msg, TransferError, fragments, parse_chunk, pull, tag
from raceforge.capture.usb import OUTBOX, STATUS, FolderFiles, pull_usb
from tests.capture.fake_phone import FakePhone, link

KEY = "laptop-key-0123456789"


def _data(n: int, seed: int = 1) -> bytes:
    return bytes((i * 31 + seed) % 251 for i in range(n))


async def _session(phone: FakePhone, inbox: Inbox, key: str = KEY, mtu: int = 180) -> list[str]:
    laptop, phone_end = link(mtu)
    server = asyncio.create_task(phone.serve(phone_end))
    try:
        return await pull(laptop, key, "test-laptop", inbox)
    finally:
        laptop.disconnect()
        await server


def test_fragments_roundtrip_with_small_mtu() -> None:
    frags = fragments(Msg.OFFER, b"x" * 50, max_payload=20)
    assert len(frags) == 3 and all(len(f) <= 21 for f in frags)
    assert [f[0] for f in frags] == [0, 0, 1]
    assert b"".join(f[1:] for f in frags) == bytes([Msg.OFFER]) + b"x" * 50


def test_bluetooth_transfer_two_passes(tmp_path: Path) -> None:
    passes = {"pass-a": _data(150_000), "pass-b": _data(10, seed=7)}
    phone = FakePhone(KEY, passes, chunk=4096)
    inbox = Inbox(tmp_path / "inbox")
    done = asyncio.run(_session(phone, inbox, mtu=20))  # tiny MTU: many fragments per chunk
    assert done == ["pass-a", "pass-b"] and phone.delivered == ["pass-a", "pass-b"]
    for pid, data in passes.items():
        assert inbox.path(pid).read_bytes() == data
        item = inbox.get(pid)
        assert item.state == "waiting" and item.source == "bluetooth"
        assert item.slug == "scan-corridor-2nd-floor"


def test_bluetooth_resumes_after_disconnect(tmp_path: Path) -> None:
    data = _data(100_000)
    phone = FakePhone(KEY, {"p1": data}, chunk=4096, drop_after_chunks=10)
    inbox = Inbox(tmp_path / "inbox")
    with pytest.raises(TransferError, match="connection lost"):
        asyncio.run(_session(phone, inbox))
    assert inbox.get("p1").state == "receiving"
    assert inbox.part_path("p1").stat().st_size == 10 * 4096
    first = phone.sent_bytes
    assert asyncio.run(_session(phone, inbox)) == ["p1"]
    assert phone.sent_bytes - first == len(data) - 10 * 4096  # nothing sent twice
    assert inbox.path("p1").read_bytes() == data


def test_unpaired_laptop_is_rejected(tmp_path: Path) -> None:
    phone = FakePhone(KEY, {"p1": _data(1000)})
    inbox = Inbox(tmp_path / "inbox")
    with pytest.raises(TransferError, match="unpaired"):
        asyncio.run(_session(phone, inbox, key="some-other-laptop"))
    assert inbox.list() == []


def test_passes_tagged_for_another_laptop_are_skipped(tmp_path: Path) -> None:
    phone = FakePhone(KEY, {"p1": _data(1000)}, offer_key="other-laptop")
    inbox = Inbox(tmp_path / "inbox")
    assert asyncio.run(_session(phone, inbox)) == []
    assert inbox.list() == []


def test_corrupt_chunk_is_detected(tmp_path: Path) -> None:
    phone = FakePhone(KEY, {"p1": _data(20_000)}, chunk=4096, corrupt_chunk=2)
    inbox = Inbox(tmp_path / "inbox")
    with pytest.raises(TransferError, match="CRC"):
        asyncio.run(_session(phone, inbox))
    assert inbox.part_path("p1").stat().st_size == 2 * 4096  # good chunks kept for the resume


def test_parse_chunk_rejects_short_body() -> None:
    with pytest.raises(TransferError):
        parse_chunk(b"\x00" * 5)


def test_inbox_discards_a_file_with_the_wrong_checksum(tmp_path: Path) -> None:
    inbox = Inbox(tmp_path / "inbox")
    offer = OfferedPass(id="p1", size=3, sha256="0" * 64, project="X", tag="t")
    inbox.begin(offer, "usb")
    inbox.append("p1", b"abc")
    with pytest.raises(ValueError, match="checksum"):
        inbox.complete("p1")
    assert not inbox.part_path("p1").exists() and inbox.get("p1").received == 0


# ----------------------------------------------------------------- cable (USB)
def _phone_folder(root: Path, passes: dict[str, bytes], key: str = KEY) -> FolderFiles:
    entries: list[dict[str, object]] = []
    for pid, data in passes.items():
        rel = f"Projects/corridor/{pid}.tscan"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        entries.append(
            {
                "id": pid,
                "file": rel,
                "size": len(data),
                "sha256": sha,
                "project": "Corridor",
                "pass_type": "low",
                "tag": tag(key, pid, sha),
            }
        )
    (root / OUTBOX).parent.mkdir(parents=True, exist_ok=True)
    (root / OUTBOX).write_text(json.dumps({"v": 1, "phone": "iPhone", "passes": entries}))
    return FolderFiles(root)


def test_usb_copies_passes_and_reports_status(tmp_path: Path) -> None:
    data = _data(9 * 1024 * 1024)  # three 4 MiB reads
    device = _phone_folder(tmp_path / "phone", {"p1": data})
    inbox = Inbox(tmp_path / "inbox")
    seen: list[int] = []
    done = asyncio.run(pull_usb(device, KEY, "laptop", inbox, lambda _i, r, _s: seen.append(r)))
    assert done == ["p1"] and inbox.path("p1").read_bytes() == data
    assert len(seen) == 4 and seen[-1] == len(data)
    status = json.loads((tmp_path / "phone" / STATUS).read_text())
    assert status["passes"]["p1"] == {"received": len(data), "size": len(data), "done": True}
    assert inbox.get("p1").source == "usb"


def test_usb_resumes_a_partial_copy(tmp_path: Path) -> None:
    data = _data(5 * 1024 * 1024)
    device = _phone_folder(tmp_path / "phone", {"p1": data})
    inbox = Inbox(tmp_path / "inbox")
    sha = hashlib.sha256(data).hexdigest()
    inbox.begin(OfferedPass(id="p1", size=len(data), sha256=sha, project="Corridor", tag=""), "usb")
    inbox.append("p1", data[:1_000_000])
    reads: list[int] = []
    orig = device.read

    async def counting(path: str, offset: int, size: int) -> bytes:
        reads.append(offset)
        return await orig(path, offset, size)

    device.read = counting  # type: ignore[method-assign]
    assert asyncio.run(pull_usb(device, KEY, "laptop", inbox)) == ["p1"]
    assert reads[0] == 1_000_000 and inbox.path("p1").read_bytes() == data


def test_usb_unpaired_laptop_gets_nothing(tmp_path: Path) -> None:
    device = _phone_folder(tmp_path / "phone", {"p1": _data(100)}, key="other-laptop")
    inbox = Inbox(tmp_path / "inbox")
    assert asyncio.run(pull_usb(device, KEY, "laptop", inbox)) == []
    assert inbox.list() == []


def test_usb_rejects_paths_outside_documents(tmp_path: Path) -> None:
    device = _phone_folder(tmp_path / "phone", {"p1": _data(100)})
    outbox = json.loads((tmp_path / "phone" / OUTBOX).read_text())
    outbox["passes"][0]["file"] = "../../etc/passwd"
    (tmp_path / "phone" / OUTBOX).write_text(json.dumps(outbox))
    with pytest.raises(ValueError, match="unsafe path"):
        asyncio.run(pull_usb(device, KEY, "laptop", Inbox(tmp_path / "inbox")))


def test_usb_without_outbox_does_nothing(tmp_path: Path) -> None:
    (tmp_path / "phone").mkdir()
    inbox = Inbox(tmp_path / "inbox")
    assert asyncio.run(pull_usb(FolderFiles(tmp_path / "phone"), KEY, "l", inbox)) == []


# ------------------------------------------- Python laptop ↔ Swift phone (AC9, cross-language)
class PipeChannel:
    """Fragments framed as u16 big-endian length + bytes over the stdin/stdout of `rftx-phone`."""

    def __init__(self, proc: asyncio.subprocess.Process, max_payload: int) -> None:
        self.proc = proc
        self.max_payload = max_payload

    async def send(self, fragment: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(len(fragment).to_bytes(2, "big") + fragment)
        await self.proc.stdin.drain()

    async def recv(self) -> bytes:
        assert self.proc.stdout is not None
        try:
            head = await self.proc.stdout.readexactly(2)
            return await self.proc.stdout.readexactly(int.from_bytes(head, "big"))
        except asyncio.IncompleteReadError as exc:
            raise ConnectionError("phone process ended") from exc


SWIFT_PHONE = os.environ.get("RF_RFTX_PHONE")


async def _swift_session(
    phone_key: str, laptop_key: str, files: list[Path], inbox: Inbox
) -> tuple[list[str], str]:
    assert SWIFT_PHONE
    proc = await asyncio.create_subprocess_exec(
        SWIFT_PHONE,
        phone_key,
        "100",
        *map(str, files),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        done = await pull(PipeChannel(proc, 100), laptop_key, "pytest", inbox)
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        _, err = await proc.communicate()
    return done, err.decode()


@pytest.mark.skipif(
    not SWIFT_PHONE, reason="set RF_RFTX_PHONE to the built rftx-phone binary (CI: trackscout job)"
)
def test_python_laptop_pulls_from_the_swift_phone(tmp_path: Path) -> None:
    files = [tmp_path / "p1.tscan", tmp_path / "p2.tscan"]
    files[0].write_bytes(_data(200_000))
    files[1].write_bytes(_data(5, seed=3))
    inbox = Inbox(tmp_path / "inbox")
    done, log = asyncio.run(_swift_session(KEY, KEY, files, inbox))
    assert done == ["p1", "p2"]
    assert inbox.path("p1").read_bytes() == files[0].read_bytes()
    assert "delivered p1" in log and "delivered p2" in log


@pytest.mark.skipif(
    not SWIFT_PHONE, reason="set RF_RFTX_PHONE to the built rftx-phone binary (CI: trackscout job)"
)
def test_swift_phone_rejects_an_unpaired_laptop(tmp_path: Path) -> None:
    (tmp_path / "p1.tscan").write_bytes(_data(100))
    inbox = Inbox(tmp_path / "inbox")
    with pytest.raises(TransferError, match="unpaired"):
        asyncio.run(_swift_session(KEY, "wrong-key", [tmp_path / "p1.tscan"], inbox))
    assert inbox.list() == []
