"""Entry point on the EV3: ``python3 -m raceforge_ev3 [--config ev3.json]``."""

import argparse
import json
import signal

from raceforge_ev3.bridge import Bridge, open_socket
from raceforge_ev3.hw import DEFAULT_CONFIG, Ev3Hardware


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path:
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="raceforge_ev3")
    ap.add_argument("--config", help="JSON config (ports, rates); defaults in hw.DEFAULT_CONFIG")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    hw = Ev3Hardware(cfg)
    stop = {"flag": False}

    def on_signal(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    bridge = Bridge(
        hw,
        open_socket(int(cfg["port"])),
        failsafe_s=cfg["failsafe_ms"] / 1000.0,
        period_s=1.0 / float(cfg["rate_hz"]),
    )
    try:
        bridge.run(lambda: stop["flag"])
    finally:
        hw.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
