#!/bin/bash
set -euo pipefail
desktop="$(cd -P "$(dirname "$0")/.." && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/trellin-tests.XXXXXXXX")"
trap 'rm -rf "$scratch"' EXIT
xcrun swiftc -module-cache-path "$scratch/modules" -parse-as-library \
    "$desktop/Sources/WorkbenchProcess.swift" "$desktop/tests/WorkbenchProcessTests.swift" \
    -o "$scratch/trellin-tests"
"$scratch/trellin-tests"
