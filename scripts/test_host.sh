#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p native/build/module-cache
xcrun swiftc -swift-version 5 -target arm64-apple-macos15.0 -module-cache-path native/build/module-cache \
  native/Host/HostCore.swift native/Host/ServiceClient.swift native/HostTests/main.swift -o native/build/HostTests
native/build/HostTests
