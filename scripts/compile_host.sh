#!/bin/bash
# Local development bundle; no installation, capture permission or service start.
set -euo pipefail
cd "$(dirname "$0")/.."
APP="${TINGJI_HOST_COMPILE_APP:?Internal isolated build path required}"
[[ "$APP" == */.tingji-host-releases/*/Tingji.app && ! -e "$APP" ]] || exit 2
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" native/build/module-cache
xcrun swiftc -swift-version 5 -O -target arm64-apple-macos15.0 -module-cache-path native/build/module-cache \
  native/Host/HostCore.swift native/Host/HostPreferences.swift native/Host/ServiceClient.swift native/Host/HostSettingsController.swift native/Host/WorkspacePolicy.swift native/Host/WorkspaceDownloads.swift native/Host/WorkspaceController.swift native/Host/CompanionController.swift native/Host/AppDelegate.swift native/Host/main.swift \
  -o "$APP/Contents/MacOS/TingjiHost"
cp native/Host/workspace-permissions.js "$APP/Contents/Resources/workspace-permissions.js"
cp native/Host/Tingji.icns "$APP/Contents/Resources/Tingji.icns"
identifier=cn.seeknow.tingji.host
if [[ -n "${TINGJI_PORTABLE_RUNTIME_SOURCE:-}" ]]; then
  runtime="$APP/Contents/Resources/PortableRuntime"
  test -x "$TINGJI_PORTABLE_RUNTIME_SOURCE/python"
  test -d "$TINGJI_PORTABLE_RUNTIME_SOURCE/capture/TingjiCapture.app"
  test -f "${TINGJI_PORTABLE_STATIC:?Standalone frontend build required}/index.html"
  ditto "$TINGJI_PORTABLE_RUNTIME_SOURCE" "$runtime"
  rsync -a --delete --exclude=static --exclude=__pycache__ app/ "$runtime/code/app/"
  rm -rf -- "$runtime/code/app/static"
  ditto "$TINGJI_PORTABLE_STATIC" "$runtime/code/app/static"
  cp scripts/manage_service.py scripts/manage_backups.py scripts/host_delivery.py \
    scripts/update_standalone.py "$runtime/code/scripts/"
  for mode in backend qwen; do
    venv=.venv
    [[ "$mode" != qwen ]] || venv=.venv-qwen
    mkdir -p "$runtime/code/$venv/bin"
    {
      cat <<'SH'
#!/bin/sh
runtime=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")/../../.." && pwd)
SH
      printf 'exec "$runtime/python" %s "$@"\n' "$mode"
    } > "$runtime/code/$venv/bin/python"
    chmod +x "$runtime/code/$venv/bin/python"
  done
  "$runtime/python" qwen -c 'import json,sys; from importlib.metadata import version; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps({name: version(name) for name in ("mlx-audio", "mlx", "mlx-metal", "transformers", "tokenizers", "huggingface-hub", "numpy")}))' "$runtime/code/.venv-qwen/qwen-runtime.json"
  identifier=cn.seeknow.tingji.host.standalone
fi
python3 - "$APP" "$PWD" "$identifier" <<'PY'
import json,os,plistlib,sys
from pathlib import Path
app,root=map(Path,sys.argv[1:3]);root=root.resolve()
identifier=sys.argv[3]
port=int(os.getenv('PORT','8765'))
if not 1024<=port<=65535:raise SystemExit('PORT 必须在 1024～65535 之间')
config={'version':1,'projectRoot':str(root),'dataRoot':str(Path(os.getenv('MEETING_DATA',str(root/'data'))).expanduser().resolve()),'backupsRoot':str(Path(os.getenv('MEETING_BACKUPS',str(root/'backups'))).expanduser().resolve()),'port':port}
(app/'Contents/Resources/host-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
with (app/'Contents/Info.plist').open('wb') as f:
    info={'CFBundleIdentifier':identifier,'CFBundleName':'听记','CFBundleDisplayName':'听记宿主','CFBundleExecutable':'TingjiHost','CFBundlePackageType':'APPL','CFBundleIconFile':'Tingji.icns','CFBundleVersion':'4','CFBundleShortVersionString':'0.4.0','LSMinimumSystemVersion':'15.0','NSHighResolutionCapable':True}
    info['NSMicrophoneUsageDescription']='听记仅在你开启麦克风录音时采集声音，用于保存现场音轨和生成会议文字。'
    if os.getenv('TINGJI_PORTABLE_RUNTIME_SOURCE'):
        info['TingjiPortableRuntime']=True
        if build := os.getenv('TINGJI_INTERNAL_BUILD'):
            if not build.isascii() or not build.isdecimal() or int(build)<6:
                raise SystemExit('内部构建号无效')
            info['CFBundleVersion']=build
        config.update(projectRoot='/PortableRuntime/code',dataRoot='/data',backupsRoot='/backups')
        (app/'Contents/Resources/host-config.json').write_text(json.dumps(config,indent=2))
        info['CFBundleDisplayName']='听记独立工作台'
    if os.getenv('TINGJI_HOST_STATE_DIR'):
        value=Path(os.environ['TINGJI_HOST_STATE_DIR']).expanduser()
        if not value.is_absolute():raise SystemExit('宿主配置目录必须是绝对路径')
        info['TingjiHostStateDirectory']=str(value.resolve())
    plistlib.dump(info,f)
PY
if [[ -z "${TINGJI_PORTABLE_RUNTIME_SOURCE:-}" ]]; then
  python3 scripts/host_delivery.py manifest --app "$APP"
fi
# Generated bundle only: cloud-sync metadata can otherwise prevent local signing.
xattr -cr "$APP"
codesign --force --sign - --identifier "$identifier" "$APP"
codesign --verify --deep --strict "$APP"
echo "原生宿主已构建：$APP"
