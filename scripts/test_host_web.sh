#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Requires scripts/host_ui_fixture.py at the matching port/data, never production.
: "${TINGJI_HOST_WEB_APP:?Set isolated synthetic .app path}"
[[ "$TINGJI_HOST_WEB_APP" == /private/tmp/*/Tingji.app ]] || exit 2
export TINGJI_HOST_APP="$TINGJI_HOST_WEB_APP"
[[ "${PORT:-}" == 9876 && "${MEETING_DATA:-}" == /private/tmp/* ]] || exit 2
bash scripts/build_host.sh >/dev/null
xcrun swiftc -swift-version 5 -target arm64-apple-macos15.0 -module-cache-path native/build/module-cache \
 native/Host/HostCore.swift native/Host/WorkspacePolicy.swift native/Host/HostPreferences.swift native/Host/WorkspaceController.swift native/Host/WorkspaceDownloads.swift native/HostWebTests/main.swift \
 -o "$TINGJI_HOST_WEB_APP/Contents/MacOS/TingjiHost"
codesign --force --sign - "$TINGJI_HOST_WEB_APP" >/dev/null
"$TINGJI_HOST_WEB_APP/Contents/MacOS/TingjiHost"
