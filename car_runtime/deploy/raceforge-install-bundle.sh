#!/bin/sh
# RaceForge bundle installer (spec 0005 "Deploy"), root-owned on the board. Started by the deploy
# user's forced SSH command (via its one sudo rule: `--stdin`) and by the USB auto-install.
exec /opt/raceforge/venv/bin/python -m raceforge.car.install "$@"
