#!/bin/sh
# Spec 0007 AC2: TrackScout stays free — no third-party Swift packages, no paid capabilities.
set -eu
cd "$(dirname "$0")/../trackscout_ios"

fail=0
# Package.swift may not declare dependencies on other packages.
if grep -nE '\.package\(' TrackScoutKit/Package.swift; then
    echo "TrackScoutKit must not depend on third-party packages" >&2
    fail=1
fi
# project.yml may only reference the local TrackScoutKit package.
if awk '/^packages:/{p=1;next} /^[^ ]/{p=0} p && /^  [A-Za-z]/' project.yml | grep -vE '^  TrackScoutKit:$'; then
    echo "project.yml may only use the local TrackScoutKit package" >&2
    fail=1
fi
# Capabilities that need a paid Apple Developer Program membership.
if grep -rnE 'com\.apple\.developer\.(icloud|associated-domains|applesignin|in-app-payments|healthkit|siri|networking\.networkextension|push|usernotifications\.time-sensitive)|aps-environment' \
    project.yml TrackScout Config; then
    echo "paid capability found (TrackScout must install with a free Apple ID)" >&2
    fail=1
fi
exit $fail
