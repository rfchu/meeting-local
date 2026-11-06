#!/bin/zsh
set -e
cd "$(dirname "$0")"
# An existing host owns its saved connection. Reopen it without rewriting its running bundle.
APP="${TINGJI_HOST_APP:-$HOME/Library/Caches/cn.seeknow.tingji/Tingji.app}"
if [[ -x "$APP/Contents/MacOS/TingjiHost" ]] && "$APP/Contents/MacOS/TingjiHost" --activate 2>/dev/null; then
  exit 0
fi
bash scripts/build_host.sh
exec open "$APP"
