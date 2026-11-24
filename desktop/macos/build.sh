#!/bin/bash
# Build a native Apple Silicon app for local review or release.
set -euo pipefail

fail() {
    printf 'Trellin build: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage: %s [--dev | --trial | --release | --bundle-id ID] [--print-bundle-id] EXISTING_OUTPUT_DIRECTORY\n' "$0"
}

bundle_id="org.researchmultiplier.trellin.dev"
print_bundle_id=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help)
            usage
            exit 0
            ;;
        --dev)
            bundle_id="org.researchmultiplier.trellin.dev"
            shift
            ;;
        --trial)
            bundle_id="org.researchmultiplier.trellin.trial"
            shift
            ;;
        --release)
            bundle_id="org.researchmultiplier.trellin"
            shift
            ;;
        --bundle-id)
            [[ $# -ge 2 ]] || fail "missing value for --bundle-id"
            bundle_id="$2"
            shift 2
            ;;
        --print-bundle-id)
            print_bundle_id=1
            shift
            ;;
        --)
            shift
            break
            ;;
        -*)
            fail "unknown option: $1"
            ;;
        *)
            break
            ;;
    esac
done

if [[ ! "$bundle_id" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]]; then
    fail "invalid bundle identifier: $bundle_id"
fi
if [[ $print_bundle_id -eq 1 ]]; then
    printf '%s\n' "$bundle_id"
    exit 0
fi

[[ $# -eq 1 ]] || fail "pass an existing output directory outside this checkout"
case "$1" in
    /*) output_path="$1" ;;
    *) output_path="$PWD/$1" ;;
esac
output="$(cd -P "$output_path" && pwd)" || fail "output directory does not exist"
desktop="$(cd -P "$(dirname "$0")" && pwd)"
repo="$(cd -P "$desktop/../.." && pwd)"
case "$output/" in
    "$repo/"*) fail "select an output directory outside the software checkout" ;;
esac
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || fail "build on an Apple Silicon Mac"
xcrun --find swiftc >/dev/null || fail "install the Xcode Command Line Tools"
[[ ! -e "$output/Trellin.app" && ! -L "$output/Trellin.app" ]] || fail "Trellin.app already exists in the output directory"
[[ -f "$repo/research_multiplier/workbench/app/index.html" ]] || fail "build the workbench interface first"
swift_sources=("$desktop"/Sources/*.swift)
[[ -f "${swift_sources[0]}" ]] || fail "the native Swift sources are missing"

stage="$(mktemp -d "$output/.trellin-build.XXXXXXXX")"
trap 'rm -rf "$stage"' EXIT
app="$stage/Trellin.app"
resources="$app/Contents/Resources"
source="$stage/source"
wheels="$stage/wheels"
mkdir -p "$resources" "$app/Contents/MacOS" "$source" "$wheels"

# Build from the working source, excluding ignored files and unrelated fixtures.
# This keeps setuptools build and egg-info files outside the checkout.
git -C "$repo" ls-files -z --cached --others --exclude-standard -- \
    pyproject.toml MANIFEST.in README.md LICENSE NOTICE lean research_multiplier |
while IFS= read -r -d '' file; do
    if [[ -f "$repo/$file" ]]; then
        printf '%s\0' "$file"
    fi
done |
    tar -c -f - -C "$repo" --no-recursion --null -T - |
    tar -x -f - -C "$source"

python_url='https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only_stripped.tar.gz'
python_sha256='2f11b8aa3726bdbd87dd9eb76568412faddf4f47e26913fa45d6a2e424972acd'
curl --fail --location --silent --show-error --retry 2 --proto '=https' --proto-redir '=https' \
    --output "$stage/python.tar.gz" "$python_url"
printf '%s  %s\n' "$python_sha256" "$stage/python.tar.gz" | shasum -a 256 --check
tar -xzf "$stage/python.tar.gz" -C "$resources"
python="$resources/python/bin/python3"

# Keep build-only setuptools and local caches outside the bundled runtime.
export PIP_CONFIG_FILE=/dev/null
pip=("$python" -I -B -m pip --isolated --disable-pip-version-check --no-cache-dir)
"${pip[@]}" install --only-binary=:all: --require-hashes --no-compile --target "$stage/build-tools" \
    -r "$desktop/packaging/build-requirements.lock"
PYTHONPATH="$stage/build-tools" PYTHONNOUSERSITE=1 "$python" -B -m pip --isolated \
    --disable-pip-version-check --no-cache-dir wheel --no-deps --no-build-isolation \
    --wheel-dir "$wheels" "$source"
set -- "$wheels"/research_multiplier-*.whl
[[ $# -eq 1 && -f "$1" ]] || fail "could not find the built Research Multiplier wheel"
"${pip[@]}" install --only-binary=:all: --require-hashes --no-compile \
    -r "$desktop/packaging/runtime-requirements.lock"
"${pip[@]}" install --no-deps --no-index --no-compile "$1"
"${pip[@]}" check

"$python" -I -B -c '
from importlib.metadata import version
from research_multiplier.workbench.server import _app_assets
from psycopg import pq
if pq.__impl__ != "binary" or "/" not in _app_assets():
    raise SystemExit("The bundled libpq or workbench interface is missing")
print(version("research-multiplier"))
' > "$stage/version"
version="$(cat "$stage/version")"

# The wheel entry points have absolute build-time shebangs. The app starts the
# bundled interpreter directly, so omit those wrappers.
find "$resources/python/bin" -mindepth 1 -maxdepth 1 ! -name 'python*' -delete

if [[ -d "$desktop/Resources" ]]; then
    cp -R "$desktop/Resources/." "$resources/"
fi
xcrun swift -module-cache-path "$stage/swift-modules" "$desktop/packaging/render-icon.swift" "$resources/Trellin.icns"
cp "$desktop/packaging/Info.plist" "$app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set CFBundleIdentifier $bundle_id" "$app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set CFBundleShortVersionString $version" "$app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set CFBundleVersion $version" "$app/Contents/Info.plist"
xcrun swiftc -O -target arm64-apple-macos15.0 -module-cache-path "$stage/swift-modules" \
    -o "$app/Contents/MacOS/Trellin" "${swift_sources[@]}"

codesign --force --sign - --timestamp=none "$app"
codesign --verify --deep --strict "$app"
mv -n "$app" "$output/"
[[ ! -d "$app" ]] || fail "another build installed Trellin.app in the output directory"
printf '%s\n' "$output/Trellin.app"
