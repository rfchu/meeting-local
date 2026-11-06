#!/bin/bash
# Local development bundle; no installation, capture permission or service start.
set -euo pipefail
cd "$(dirname "$0")/.."
APP="${TINGJI_HOST_COMPILE_APP:?Internal isolated build path required}"
[[ "$APP" == */.tingji-host-releases/*/Tingji.app && ! -e "$APP" ]] || exit 2
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" native/build/module-cache
xcrun swiftc -swift-version 5 -O -target arm64-apple-macos15.0 -module-cache-path native/build/module-cache \
  native/Host/HostCore.swift native/Host/HostPreferences.swift native/Host/ServiceClient.swift native/Host/HostSettingsController.swift native/Host/WorkspacePolicy.swift native/Host/WorkspaceDownloads.swift native/Host/WorkspaceController.swift native/Host/AppDelegate.swift native/Host/main.swift \
  -o "$APP/Contents/MacOS/TingjiHost"
cp native/Host/workspace-permissions.js "$APP/Contents/Resources/workspace-permissions.js"
python3 - "$APP" "$PWD" <<'PY'
import json,os,plistlib,sys
from pathlib import Path
app,root=map(Path,sys.argv[1:]);root=root.resolve()
port=int(os.getenv('PORT','8765'))
if not 1024<=port<=65535:raise SystemExit('PORT 必须在 1024～65535 之间')
config={'version':1,'projectRoot':str(root),'dataRoot':str(Path(os.getenv('MEETING_DATA',str(root/'data'))).expanduser().resolve()),'backupsRoot':str(Path(os.getenv('MEETING_BACKUPS',str(root/'backups'))).expanduser().resolve()),'port':port}
(app/'Contents/Resources/host-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
with (app/'Contents/Info.plist').open('wb') as f:
    info={'CFBundleIdentifier':'cn.seeknow.tingji.host','CFBundleName':'听记','CFBundleDisplayName':'听记宿主','CFBundleExecutable':'TingjiHost','CFBundlePackageType':'APPL','CFBundleVersion':'4','CFBundleShortVersionString':'0.4.0','LSMinimumSystemVersion':'15.0','NSHighResolutionCapable':True}
    if os.getenv('TINGJI_HOST_STATE_DIR'):
        value=Path(os.environ['TINGJI_HOST_STATE_DIR']).expanduser()
        if not value.is_absolute():raise SystemExit('宿主配置目录必须是绝对路径')
        info['TingjiHostStateDirectory']=str(value.resolve())
    plistlib.dump(info,f)
PY
python3 scripts/host_delivery.py manifest --app "$APP"
# Generated bundle only: cloud-sync metadata can otherwise prevent local signing.
xattr -cr "$APP"
codesign --force --sign - --identifier cn.seeknow.tingji.host "$APP"
codesign --verify --deep --strict "$APP"
echo "原生宿主已构建：$APP"
