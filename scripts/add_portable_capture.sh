#!/bin/bash
# Attach the D02 development helper to a D01 probe without replacing the installed capture app.
set -euo pipefail
cd "$(dirname "$0")/.."
bundle="$(cd "${1:?Usage: add_portable_capture.sh RUNTIME_PROBE}" && pwd -P)"
[[ -x "$bundle/python" && -d "$bundle/code/app" ]] || exit 2
target="$bundle/capture/TingjiCapture.app"
if [[ -e "$target" || -L "$target" ]]; then
  echo "The probe already has a capture helper" >&2
  exit 2
fi
stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
app="$stage/.tingji-capture-releases/probe/TingjiCapture.app"
TINGJI_CAPTURE_PROBE_BUILD=1 TINGJI_CAPTURE_COMPILE_APP="$app" bash scripts/compile_capture.sh
/usr/bin/codesign --verify --deep --strict "$app"
mkdir -p "$bundle/capture"
mv "$app" "$target"
cp scripts/verify_portable_capture.py "$bundle/code/scripts/"
printf 'D02 helper: %s\n' "$target"
