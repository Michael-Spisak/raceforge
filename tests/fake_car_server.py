"""Stand-in for the car runtime's telemetry/teleop WebSocket (spec 0005): tests and UI rehearsal.

Same messages as `rf-telemetry`: hello, telemetry at 20 Hz, acks; teleop with the 300 ms dead-man,
`teleop_release`, latched `stop`, `note`, token check. No physics: "measured" speed = command.

    uv run python tests/fake_car_server.py --port 8792 [--token TOKEN] [--mode test|race]
"""

import argparse
import asyncio
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve

DEADMAN_S = 0.3


class FakeCar:
    def __init__(self, token: str | None, mode: str) -> None:
        self.token, self.mode = token, mode
        self.teleop: dict[str, float] | None = None
        self.last = 0.0
        self.stopped = False
        self.seq = 0

    def state(self) -> tuple[str, float, float]:
        if self.stopped:
            return "stop", 0.0, 0.0
        if self.teleop is None:
            return "run", 0.0, 0.0
        if time.monotonic() - self.last > DEADMAN_S:
            return "deadman_stop", 0.0, 0.0
        return "teleop", self.teleop["steer"], self.teleop["speed"]

    def frame(self) -> dict[str, Any]:
        state, steer, speed = self.state()
        self.seq += 1
        return {
            "schema": "telemetry",
            "schema_version": 1,
            "seq": self.seq,
            "mode": self.mode,
            "t": {"mono_ns": time.monotonic_ns()},
            "state": state,
            "faults": [],
            "cmd": {"steering_rad": steer, "speed_m_s": speed},
            "meas": {"steering_rad": steer, "speed_m_s": speed},
            "power": {"ev3_battery_v": 7.9},
            "loop": {"rate_hz": 50.0, "jitter_ms": 0.4},
        }

    async def handle(self, conn: ServerConnection) -> None:
        query = parse_qs(urlsplit(conn.request.path if conn.request else "/").query)
        if self.token and query.get("token", [""])[0] != self.token:
            await conn.close(1008, "bad token")
            return
        await conn.send(
            json.dumps({"type": "hello", "car": "fake-car", "mode": self.mode, "rate_hz": 20})
        )

        async def telemetry() -> None:
            while True:
                await conn.send(json.dumps({"type": "telemetry", "frame": self.frame()}))
                await asyncio.sleep(0.05)

        task = asyncio.create_task(telemetry())
        try:
            async for raw in conn:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "teleop":
                    if self.mode == "race":
                        await conn.send(
                            json.dumps(
                                {"type": "ack", "cmd": "teleop", "ok": False, "detail": "race mode"}
                            )
                        )
                        continue
                    self.teleop = {"steer": float(msg["steer"]), "speed": float(msg["speed"])}
                    self.last = time.monotonic()
                elif kind == "teleop_release":
                    self.teleop = None
                elif kind == "stop":
                    self.stopped = True
                    self.teleop = None
                    await conn.send(
                        json.dumps({"type": "ack", "cmd": "stop", "ok": True, "detail": ""})
                    )
                    await conn.send(
                        json.dumps(
                            {
                                "type": "event",
                                "t": {"mono_ns": 0},
                                "kind": "operator_stop",
                                "detail": msg.get("reason", ""),
                            }
                        )
                    )
                elif kind == "note":
                    await conn.send(
                        json.dumps({"type": "ack", "cmd": "note", "ok": True, "detail": ""})
                    )
                    await conn.send(
                        json.dumps(
                            {
                                "type": "event",
                                "t": {"mono_ns": 0},
                                "kind": "note",
                                "detail": msg.get("text", ""),
                            }
                        )
                    )
        finally:
            task.cancel()


async def main(port: int, token: str | None, mode: str) -> None:
    car = FakeCar(token, mode)
    async with serve(car.handle, "127.0.0.1", port):
        print(f"fake car on ws://127.0.0.1:{port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--token")
    ap.add_argument("--mode", default="test", choices=["test", "race"])
    a = ap.parse_args()
    asyncio.run(main(a.port, a.token, a.mode))
