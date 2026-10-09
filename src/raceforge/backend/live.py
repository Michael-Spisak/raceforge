"""Live telemetry relay (spec 0027): one publisher per session, many read-only watchers.

Sessions live in memory of the active API container (blue-green keeps one active), so nothing
touches the database. The publisher never waits for watchers: each watcher has a bounded queue and
a slow watcher loses its oldest messages.
"""

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from raceforge.backend.models import LiveSession

MAX_MESSAGE = 64 * 1024
MAX_WATCHERS = 20
WATCH_QUEUE = 50
PUBLISHER_IDLE_S = 30.0

Queue = asyncio.Queue[str | None]  # None = the session ended


@dataclass
class _Session:
    id: str
    workspace_id: str
    car: str
    publisher: str
    started_at: datetime
    last_message_at: datetime
    messages: int = 0
    hello: str | None = None
    last_frame: str | None = None
    watchers: set[Queue] = field(default_factory=set[Queue])

    def info(self) -> LiveSession:
        return LiveSession(
            id=self.id,
            workspace_id=self.workspace_id,
            car=self.car,
            publisher=self.publisher,
            started_at=self.started_at,
            last_message_at=self.last_message_at,
            messages=self.messages,
        )


def _put(q: Queue, item: str | None) -> None:
    while True:
        try:
            q.put_nowait(item)
            return
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()  # drop the oldest: the watcher is too slow


class LiveHub:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    def list(self, workspace_id: str) -> list[LiveSession]:
        rows = [s for s in self._sessions.values() if s.workspace_id == workspace_id]
        return [s.info() for s in sorted(rows, key=lambda s: s.started_at)]

    def get(self, session_id: str) -> LiveSession | None:
        s = self._sessions.get(session_id)
        return s.info() if s else None

    def start(self, workspace_id: str, car: str, publisher: str, now: datetime) -> LiveSession:
        s = _Session(uuid.uuid4().hex[:12], workspace_id, car[:64], publisher, now, now)
        self._sessions[s.id] = s
        return s.info()

    def publish(self, session_id: str, text: str, now: datetime) -> None:
        s = self._sessions.get(session_id)
        if s is None or len(text) > MAX_MESSAGE:
            return
        try:
            kind = json.loads(text).get("type")
        except (ValueError, AttributeError):
            return
        if kind == "hello":
            s.hello = text
        elif kind == "telemetry":
            s.last_frame = text
        s.messages += 1
        s.last_message_at = now
        for q in s.watchers:
            _put(q, text)

    def end(self, session_id: str) -> None:
        s = self._sessions.pop(session_id, None)
        if s is not None:
            for q in s.watchers:
                _put(q, None)

    def watch(self, session_id: str) -> Queue | None:
        """A queue that first holds the last hello and frame; None if unknown or full."""
        s = self._sessions.get(session_id)
        if s is None or len(s.watchers) >= MAX_WATCHERS:
            return None
        q: Queue = asyncio.Queue(WATCH_QUEUE)
        for text in (s.hello, s.last_frame):
            if text is not None:
                q.put_nowait(text)
        s.watchers.add(q)
        return q

    def unwatch(self, session_id: str, q: Queue) -> None:
        s = self._sessions.get(session_id)
        if s is not None:
            s.watchers.discard(q)
