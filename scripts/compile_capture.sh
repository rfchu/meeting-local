#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
APP="${TINGJI_CAPTURE_COMPILE_APP:?Internal isolated build path required}"
[[ "$APP" == */.tingji-capture-releases/*/TingjiCapture.app && ! -e "$APP" ]] || exit 2
mkdir -p "$APP/Contents/MacOS" native/build/module-cache
xcrun swiftc -swift-version 5 -O -target arm64-apple-macos15.0 -module-cache-path native/build/module-cache \
  native/AudioCapture/main.swift native/AudioCapture/Diagnostics.swift native/AudioCapture/Ownership.swift native/AudioCapture/ArtifactLease.swift -o "$APP/Contents/MacOS/TingjiCapture"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>cn.seeknow.tingji.capture</string>
<key>CFBundleName</key><string>听记采集器</string>
<key>CFBundleExecutable</key><string>TingjiCapture</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>4</string>
<key>LSUIElement</key><true/>
<key>LSMinimumSystemVersion</key><string>15.0</string>
<key>NSMicrophoneUsageDescription</key><string>听记将你的发言与线上会议声音分别保存在本机，用于会议转写。</string>
<key>NSScreenCaptureUsageDescription</key><string>听记使用系统采集接口录制电脑声音，仅保存音频，不保存屏幕画面。</string>
</dict></plist>
PLIST
xattr -cr "$APP"
codesign --force --sign - --identifier cn.seeknow.tingji.capture "$APP"
echo "原生采集器已构建：$APP"
