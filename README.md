# 听记 · 本地会议工作台

听记支持中文和英文会议。在 Mac 上录音或导入已有录音，选择自动识别、中文或英文转写，回听核对后整理成可交付的会议纪要。原文语言与 AI 纪要输出语言可以分别设置。

![听记录音工作台](assets/screenshots/01-recording-workbench.png)

## 能做什么

- **录音与转写**：会中记录重点和待办，查看实时文字。会后可复用已保存的识别片段快速完成，也可重新精细转写。支持 Whisper 和可选的 Qwen3-ASR。
- **核对与交付**：对照原文和录音，检查发言人、引用和行动项。生成纪要后可比较版本、选用差异并保留交付版本，支持 Word、PDF 和 Markdown 导出。
- **会议资料库**：按项目、客户或系列归类，跟进未完成事项。支持中英文关键词、本地语义检索与带来源的跨会议问答；回答可独立选择中文或英文，引用保持原文。

## 快速开始

本项目目前以 Apple Silicon Mac 上的源码部署为主。准备 Python 3.12 或 3.13、Xcode 命令行工具和 FFmpeg。使用 macOS 15 或更新版本可构建原生双音轨采集器。仓库包含构建好的网页资源，直接使用时无需 Node.js。

获取仓库后，在仓库根目录运行：

```bash
bash scripts/setup.sh
./service.command start
```

安装脚本需要联网下载依赖和模型。启动后打开 [本地工作台](http://127.0.0.1:8765/)，可以先导入一段音频，或从录音工作台开始。

按需安装 Qwen3-ASR 和原生采集器：

```bash
bash scripts/setup_qwen.sh
bash scripts/build_capture.sh
```

使用原生采集时，请按 macOS 提示授权所选音源。查看服务状态可运行 `./service.command status`，停止服务可运行 `./service.command stop`。

## 模型与数据

语音转写使用本机模型。会议分析、AI 纪要和引用问答可在「运行管理」配置本地或云端的 OpenAI 兼容模型。若选择云端模型，相关功能会按所选服务处理提交的会议内容。希望内容留在本机时，可配置本地模型。

会议数据默认保存在 `data/`，服务默认监听本机回环地址。备份、迁移和恢复使用项目提供的离线命令，参见[本机维护指南](docs/engineering/WORKSPACE.md)。

## 开发与文档

后端使用 Python、FastAPI 和 SQLite，前端使用 React、TypeScript 和 Vite。修改前端后可在项目根目录运行 `bash scripts/build_frontend.sh` 生成静态资源。

- [使用指南](docs/USER_GUIDE.md)
- [开发与验证](docs/engineering/DEVELOPMENT.md)
- [文档索引](docs/README.md)
- [项目路线图与统一验收](docs/PROJECT_ROADMAP.md)
- [本机工作空间与桌面维护](docs/engineering/WORKSPACE.md)

当前原生宿主属于本机开发交付，依赖项目源码、Python 环境和模型。独立安装包、发行签名与公证仍待完成。第三方依赖和模型适用各自的许可条款。
