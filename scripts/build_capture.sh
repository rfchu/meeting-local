#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
APP="${TINGJI_CAPTURE_APP:-$HOME/Library/Caches/cn.seeknow.tingji/TingjiCapture.app}"
exec python3 scripts/capture_delivery.py build --target "$APP"
