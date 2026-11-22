#!/bin/bash
# Render every native status screen in light and dark for review (#286).
set -euo pipefail
desktop="$(cd -P "$(dirname "$0")/.." && pwd)"
[[ $# -ge 1 && -d "$1" ]] || { echo "Usage: $0 EXISTING_OUTPUT_DIRECTORY [en|zh-Hans]" >&2; exit 2; }
scratch="$(mktemp -d "${TMPDIR:-/tmp}/trellin-render.XXXXXXXX")"
trap 'rm -rf "$scratch"' EXIT
xcrun swiftc -module-cache-path "$scratch/modules" -parse-as-library \
    "$desktop/Sources/WorkbenchProcess.swift" "$desktop/Sources/StatusPresentation.swift" "$desktop/Sources/StatusScreen.swift" \
    "$desktop/tests/render-status-screens.swift" -o "$scratch/render"
"$scratch/render" "$@"
