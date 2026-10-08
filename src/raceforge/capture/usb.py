"""Cable transfer (spec 0007 scope 9): copy passes out of TrackScout's shared Documents folder.

The phone lists passes for its paired laptop in ``TrackScoutTransfer/outbox.json`` (each with an
HMAC tag made with the pairing key); the laptop copies them in 4 MiB reads, resuming ``.part``
files, and reports its progress in ``TrackScoutTransfer/status.json`` so the phone can show it.
Device access goes through the small ``DeviceFiles`` interface: ``FolderFiles`` (tests, mounted
folders) or ``IosDeviceFiles`` (pymobiledevice3, optional extra ``transfer``).
"""

import hmac
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from raceforge.capture.inbox import Inbox, OfferedPass
from raceforge.capture.rftx import tag

OUTBOX = "TrackScoutTransfer/outbox.json"
STATUS = "TrackScoutTransfer/status.json"
READ_SIZE = 4 * 1024 * 1024
BUNDLE_SUFFIX = ".trackscout"


class DeviceFiles(Protocol):
    """Files below the app's Documents folder; paths are relative POSIX paths."""

    async def read_text(self, path: str) -> str | None: ...

    async def read(self, path: str, offset: int, size: int) -> bytes: ...

    async def write_text(self, path: str, text: str) -> None: ...


def _relative(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"unsafe path from phone: {path!r}")
    return str(p)


class FolderFiles:
    """A local folder standing in for the phone's Documents (tests, or a Finder copy)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def read_text(self, path: str) -> str | None:
        p = self.root / _relative(path)
        return p.read_text(encoding="utf-8") if p.exists() else None

    async def read(self, path: str, offset: int, size: int) -> bytes:
        with (self.root / _relative(path)).open("rb") as f:
            f.seek(offset)
            return f.read(size)

    async def write_text(self, path: str, text: str) -> None:
        p = self.root / _relative(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


async def pull_usb(
    device: DeviceFiles,
    key: str,
    laptop_name: str,
    inbox: Inbox,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Copy every pass the phone offers to this laptop; returns the ids now in the inbox."""
    raw = await device.read_text(OUTBOX)
    if raw is None:
        return []
    outbox: dict[str, Any] = json.loads(raw)
    status: dict[str, dict[str, Any]] = {}

    async def report(pass_id: str, received: int, size: int, done: bool) -> None:
        status[pass_id] = {"received": received, "size": size, "done": done}
        body = {"v": 1, "laptop": laptop_name, "passes": status}
        await device.write_text(STATUS, json.dumps(body))
        if progress:
            progress(pass_id, received, size)

    done: list[str] = []
    for entry_raw in outbox.get("passes", []):
        entry = OfferedPass.model_validate(entry_raw)
        if entry.file is None or not hmac.compare_digest(
            entry.tag, tag(key, entry.id, entry.sha256)
        ):
            continue  # not for this laptop
        source = _relative(entry.file)
        offset = inbox.begin(entry, "usb")
        while offset < entry.size:
            data = await device.read(source, offset, min(READ_SIZE, entry.size - offset))
            if not data:
                raise OSError(f"{entry.file}: file on the phone is shorter than announced")
            inbox.append(entry.id, data)
            offset += len(data)
            await report(entry.id, offset, entry.size, False)
        try:
            inbox.complete(entry.id)
        except ValueError:
            continue  # damaged: discarded, the next pull copies it again
        await report(entry.id, entry.size, entry.size, True)
        done.append(entry.id)
    return done


class IosDeviceFiles:  # pragma: no cover - needs a real iPhone (device checklist, part B)
    """TrackScout's Documents on a USB-connected iPhone/iPad via Apple's house-arrest service
    (pymobiledevice3 ≥ 11, async API): ``await IosDeviceFiles.open()``, then ``await aclose()``."""

    def __init__(self, afc: Any, name: str) -> None:
        self.afc = afc
        self.name = name

    @classmethod
    async def open(cls, bundle_id: str | None = None) -> "IosDeviceFiles":
        try:
            from pymobiledevice3.lockdown import (  # pyright: ignore[reportMissingImports]
                create_using_usbmux,  # pyright: ignore[reportUnknownVariableType]
            )
            from pymobiledevice3.services.house_arrest import (  # pyright: ignore[reportMissingImports]
                HouseArrestService,  # pyright: ignore[reportUnknownVariableType]
            )
            from pymobiledevice3.services.installation_proxy import (  # pyright: ignore[reportMissingImports]
                InstallationProxyService,  # pyright: ignore[reportUnknownVariableType]
            )
        except ImportError as exc:
            raise RuntimeError(
                "cable transfer needs the optional 'transfer' extra (pymobiledevice3)"
            ) from exc
        lockdown: Any = await create_using_usbmux()
        if bundle_id is None:
            apps: dict[str, Any] = await InstallationProxyService(lockdown=lockdown).get_apps(
                application_type="User"
            )
            matches = [b for b in apps if b.endswith(BUNDLE_SUFFIX)]
            if not matches:
                raise RuntimeError("TrackScout is not installed on the connected device")
            bundle_id = matches[0]
        afc = await HouseArrestService.create(lockdown, bundle_id, documents_only=True)
        return cls(afc, str(getattr(lockdown, "display_name", None) or "iPhone"))

    @staticmethod
    def _p(path: str) -> str:
        return f"/Documents/{_relative(path)}"

    async def read_text(self, path: str) -> str | None:
        if not await self.afc.exists(self._p(path)):
            return None
        return bytes(await self.afc.get_file_contents(self._p(path))).decode("utf-8")

    async def read(self, path: str, offset: int, size: int) -> bytes:
        handle = await self.afc.fopen(self._p(path), "r")
        try:
            await self.afc.fseek(handle, offset)
            return bytes(await self.afc.fread(handle, size))
        finally:
            await self.afc.fclose(handle)

    async def write_text(self, path: str, text: str) -> None:
        await self.afc.set_file_contents(self._p(path), text.encode("utf-8"))

    async def aclose(self) -> None:
        await self.afc.aclose()
