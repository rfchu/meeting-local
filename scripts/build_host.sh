#!/bin/bash
# Local development delivery only: no service action, capture or system installation.
set -euo pipefail
cd "$(dirname "$0")/.."
APP="${TINGJI_HOST_APP:-$HOME/Library/Caches/cn.seeknow.tingji/Tingji.app}"
exec python3 scripts/host_delivery.py build --target "$APP"
