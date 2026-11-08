# 听记独立版内部安装说明

内部版本 **0.4.0-internal.1**，App 显示版本为 0.4.0，构建号为 6。本包使用临时签名，供本机和内部验收。已实测设备为 Apple Silicon M4 Max，macOS 27.0.1。构建目标为 macOS 15 及以上，最低系统实机验收留待后续。

1. 从内部分发目录取得 `Tingji-0.4.0-internal.1.dmg` 和 `release.json`。运行 `shasum -a 256 Tingji-0.4.0-internal.1.dmg`，核对清单里的 `dmg_sha256`。
2. 打开 DMG，将 `Tingji.app` 复制到用户的「应用程序」目录（`~/Applications`），然后打开 App。若 macOS 拦截，先核对来源和哈希，再检查「系统设置 → 隐私与安全性」是否有本次启动的允许入口。
3. 在工作台按需授权麦克风和系统音频。采集失败时检查输入和输出设备，按权限提示重新打开并试录。可先录一段短音频，分别回听两轨。
4. 在「运行管理 → 模型与服务」选择随附的 `Tingji-models-0.1.0.zip`，等待校验完成，点击「下次重开启用」。结束录音并退出宿主和后台，再打开 App，确认模型包 0.1.0 已启用。转写时选择 **Qwen**。此内部包的已验证转写路径为 Qwen。
5. 录音保存后可以转写、回听和导出 TXT、SRT 或 JSON。本地或云端 LLM 可按需在运行管理配置。

内部版默认使用 `127.0.0.1:18876`，数据和备份位于 `~/Library/Application Support/cn.seeknow.tingji.host.standalone/`。迁移旧资料、更新或回退请参照[工作空间维护指南](../engineering/WORKSPACE.md)。正式签名、公证、干净机和正式权限验证的状态见 [D17 评审](standalone-release-review.md)。
