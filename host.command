#!/bin/zsh
set -e
cd "$(dirname "$0")"
# Build explicitly before launching so an old app cannot silently connect to a moved project.
bash scripts/build_host.sh
exec open "${TINGJI_HOST_APP:-$HOME/Library/Caches/cn.seeknow.tingji/Tingji.app}"
