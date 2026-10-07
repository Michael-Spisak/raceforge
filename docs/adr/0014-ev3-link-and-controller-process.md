# ADR-0014: EV3 link transport and controller process

- **Status:** accepted (refines ADR-0007)
- **Date:** 2026-10-07

## Context
ADR-0007 chose a Rust core with Python controllers "via PyO3 or shared-memory IPC" and a USB-serial link
to the EV3. Spec 0005 needs a concrete choice for both.

## Decision
- **EV3 link:** USB cable; transport = ev3dev's USB-Ethernet gadget with **UDP** binary frames (seq + CRC-16)
  at 100 Hz; USB serial (CDC-ACM) remains a fallback. EV3 stops its motors if no frame arrives for 150 ms.
- **Controller:** separate Python process (`raceforge.car.host`) talking to the Rust core over a **Unix
  domain socket** with one JSON message per tick. The Rust core owns timing, safety and the watchdog; on a
  deadline miss it stops the motors and may kill/restart the controller process.

## Consequences
- Pure-Rust binary: easy aarch64 cross-compilation (cargo-zigbuild), no Python headers needed.
- Strong isolation: a crashing/hanging controller cannot block safety functions.
- JSON per tick (~5 KB with a LiDAR scan) is well within budget at 50 Hz; switch to msgpack if profiling
  shows a need.
