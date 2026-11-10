# 听记 · 本地会议工作台

> 面向 Apple Silicon Mac 的专业本地会议与访谈工作台。支持双轨原生音频采集、端侧实时语音转写、带原文证据链的智能纪要分析，以及完全离线的本地会议资料库。所有音频与会议数据均保存在本机。

---

![会前准备与就绪舱](assets/screenshots/01-recording-workbench.png)
*图 1：会前准备与就绪舱 — 双轨音源即时试音、议程背景预设与交付策略配置*

![智能纪要与交付成果](assets/screenshots/03-meeting-outcomes.png)
*图 2：智能纪要与交付成果 — 双栏原文逐句回听核对、结构化结论分析与多版本导出*

---

## 核心特性

- **双轨原生采集与会前就绪舱**
  - 支持电脑系统声音与麦克风双轨独立采集，内置硬件级与 WebRTC AEC3 回声消除。
  - 会前提供设备检测、实时声压跳动与试录回放验证，确保音质清晰后再正式起录。
  - 原生提供置顶轻量伴随窗（快捷键 `Ctrl + Option + N`），录音状态一目了然且不遮挡主屏操作。

- **端侧多引擎实时转写**
  - 支持中英文双语自动识别与分离，本地离线运行，无需外发音频。
  - 提供高速高精度的 **Whisper large-v3-turbo** 与针对口语优化的 **Qwen3-ASR (1.7B-8bit)** 引擎，支持会中实时流式出字与会后整轨精细补转写。
  - 自动运行 Pyannote 说话人分离、3DSpeaker 声纹特征对齐与上下文标点恢复。

- **带真实证据链的智能纪要**
  - 内置标准商务会议、头脑风暴、技术研讨及结构化**在线面试**纪要模版。
  - **杜绝模型幻觉**：纪要总结与问答结论均严格锚定原文音轨片段与发言人身份，点击证据秒级定位回听。
  - 支持多版本纪要对比（Diff View），可自由选用不同版本并一键导出 Word (docx)、PDF 与 Markdown 文档。

- **桌面级运行管理控制中心**
  - 统一查看与切换本地模型包，提供模型文件完整性校验与离线能力探测。
  - 包含服务状态诊断、存储空间监控、未结任务队列管理与意外中断录音一键自愈。

- **跨会议资料库与检索问答**
  - 会议按项目、标签与日期归档，轻松跟踪跨场次行动项与责任人。
  - 支持本地中英文关键词与向量语义检索，提供带引用来源的跨会议 AI 问答。

---

## 快速上手

### 方式一：下载预编译应用与模型包（推荐）

1. 从 Release 页面下载最新版本产物（提供 [Gitea 镜像](https://git.seeknow.cn:8010/crf/meeting-local/releases/tag/v0.4.0) 或 [GitHub Releases](https://github.com/rfchu/meeting-local/releases/tag/v0.4.0)）：
   - `Tingji-v0.4.0-macOS.zip`（原生 macOS 桌面工作台，解压后拖入 `/Applications` 即可运行）
   - `TingjiCapture-v0.4.0-macOS.zip`（双轨低延迟音频采集器）
   - `Tingji-Models-v0.1.0.zip`（完整端侧离线模型包，由于单文件体积限制以 1.8GB 分卷提供 `part-aa`、`part-ab`、`part-ac`）
2. 若下载了分卷模型包，可在终端合并为完整 ZIP：
   ```bash
   cat Tingji-Models-v0.1.0.zip.part-* > Tingji-Models-v0.1.0.zip
   ```
3. 打开 `Tingji.app`，进入「运行管理 → 智能模型 → 模型与服务」，点击导入已合并的模型包 ZIP，即可完成端侧离线就绪。

### 方式二：本机源码运行（开发者）

#### 环境准备
- 操作系统：macOS 15.0+（推荐 Apple Silicon 芯片 M1/M2/M3/M4）
- 基础依赖：Python 3.12 或 3.13、Xcode Command Line Tools、FFmpeg

#### 启动服务
```bash
# 克隆仓库
git clone https://github.com/rfchu/meeting-local.git
cd meeting-local

# 初始化环境与依赖
bash scripts/setup.sh

# 启动后台服务
./service.command start
```

服务就绪后，在浏览器中访问 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)，或直接构建并打开原生桌面宿主：
```bash
# 构建原生桌面宿主
bash scripts/build_host.sh

# 启动原生应用
open ~/Library/Caches/cn.seeknow.tingji/Tingji.app
```

#### 按需扩展能力
- **Qwen3-ASR 扩展引擎**：`bash scripts/setup_qwen.sh`
- **原生双轨采集器构建**：`bash scripts/build_capture.sh`

---

## 模型与数据隐私

1. **音频与转写纯本地运行**：语音转写、说话人聚类、标点预测与声纹嵌入全部在 Mac 本机端侧芯片运行，无任何音频上传。
2. **AI 纪要与分析模型可选**：
   - 默认支持连接本地大模型（如 [LM Studio](https://lmstudio.ai/) 或 [Ollama](https://ollama.ai/)），实现 100% 离线脱网运行。
   - 亦可按需在「运行管理」配置商业 OpenAI 兼容 API 密钥。使用云端模型时需经用户明确确认，仅提交文字稿与提纲。
3. **数据自主可控**：会议数据库及所有音频原件保存在 `data/` 目录，支持离线备份与无损物理迁移。

---

## 技术架构

- **前端**：React 19 + TypeScript + Vite + TanStack Query + Virtual Scrolling
- **后端**：Python + FastAPI + SQLite (WAL 模式) + Uvicorn
- **模型推理**：`whisper.cpp` (Metal 加速) + `MLX` (Apple Silicon 深度学习框架) + `sherpa-onnx`
- **桌面原生**：Swift 5 + AppKit + WebKit + ScreenCaptureKit (原生系统音频捕获)

---

## 文档索引

- [用户使用指南](docs/USER_GUIDE.md)
- [开发与验证指南](docs/engineering/DEVELOPMENT.md)
- [本机维护与数据迁移](docs/engineering/WORKSPACE.md)
- [模型包设计规格](docs/specs/SPEC-model-packs.md)
- [产品规划路线图](docs/PROJECT_ROADMAP.md)

---

## 开源许可

本项目源码采用 MIT 许可。所包含或下载的第三方模型与工具（如 Whisper、Qwen、Pyannote、Sherpa-ONNX、FFmpeg 等）遵循各自的开源许可协议。
