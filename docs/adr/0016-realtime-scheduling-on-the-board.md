# ADR-0016: Real-time scheduling of the car runtime on the board

- **Status:** accepted (2026-10-08; v1 — v2 only via amendment after HIL)
- **Date:** 2026-10-08

## Context
Spec 0005 requires a 50 Hz control loop with p99 jitter < 2 ms on a Raspberry Pi 5, and stops the
car when the controller misses its 15 ms per-step deadline (watchdog, "stop immediately").

The runtime currently runs as an ordinary process under Linux's default scheduler (CFS/EEVDF).
Measurements on the shared development VM show why that is not enough:

- A bare 50 Hz Python sleep loop with nothing else running woke up to **17.6 ms** late (3 of 1,500
  ticks over 10 ms, kernel steal counter increasing).
- The runtime's own tests saw single stalls of 10–23 ms; one landed inside a controller step and
  tripped the deadline (`Fault::Deadline`) although the controller itself needs < 0.1 ms per step.

A Pi is not a shared VM, but the same kinds of stall exist there: other processes, kernel
threads, swapping, SD-card I/O, CPU frequency changes. During a race, a single 15 ms stall stops the
car. Nothing in the runtime asks the OS for priority today.

## Decision
**v1 (no new code, no new dependency):** the board runs `rf-runtime` as a systemd service that asks
the kernel for real-time scheduling, plus board settings that remove the usual stall sources.

- systemd unit `rf-runtime.service`:
  - `CPUSchedulingPolicy=fifo`, `CPUSchedulingPriority=50`: every thread of the runtime (control
    loop, EV3 link, LiDAR reader, logger) and the controller host process it starts (children
    inherit the policy) run SCHED_FIFO above all normal processes.
  - `CPUAffinity=2 3`: keep the runtime on two cores; the OS, network and SD card use cores 0–1.
  - `CPUSchedulingResetOnFork=no` (the default), so the controller host inherits FIFO.
- Board setup script: swap off (`dphys-swapfile` / zram disabled), CPU governor `performance`,
  kernel command line `isolcpus=2,3` so nothing else is scheduled on those cores (`nohz_full=2,3` too,
  if the board's kernel is built with `NO_HZ_FULL`).
- Kernel: the standard Raspberry Pi OS kernel. A PREEMPT_RT kernel is evaluated only if v1 misses
  the target.

**Escalation (v2, needs an amendment of this ADR):** if HIL measurements with v1 still miss
p99 < 2 ms or show deadline misses, the runtime sets priorities per thread itself:
control loop and supervisor highest, EV3 link next, LiDAR reader below, logger and controller host
lowest of the real-time threads; plus `mlockall` so no page fault can stall the loop. That needs the
**`libc`** crate (MIT OR Apache-2.0) and `unsafe` calls (`sched_setscheduler`,
`pthread_setschedparam`, `mlockall`). The workspace currently has `unsafe_code = "forbid"`, which
cannot be relaxed per module, so v2 would change it to `deny` and allow `unsafe` only in one small,
reviewed `rf-core::rt` module.

## Why not v2 now
- v1 gets most of the benefit (FIFO above all normal work, isolated cores, no swap) with zero code
  and no `unsafe`; the owner is not yet a Rust reviewer (ADR-0007).
- With one priority for all runtime threads, the logger or LiDAR reader could delay the control loop.
  They block on I/O almost immediately, so this should be small — HIL will show whether it matters.

## Consequences
- Deploy needs root once (installing the unit and kernel arguments); the runtime itself does not.
- A runaway SCHED_FIFO thread could starve the rest of the system. The kernel's RT throttling
  (`sched_rt_runtime_us`, by default 95 % of each second) keeps the board reachable; the supervisor
  thread and the EV3's own 150 ms failsafe still stop the car. Throttling is left at its default.
- CI cannot use real-time scheduling on hosted runners: timing tests keep generous bounds and run
  serially; the real numbers come from HIL (spec 0005 AC10), which must report p99 jitter and
  deadline misses with and without v1 settings.
- Follow-up: `car_runtime/deploy/rf-runtime.service` (done), the board setup script (swap,
  governor, kernel arguments; spec 0005 "Board setup"), HIL measurement; escalate to v2 only if
  HIL misses the target.
