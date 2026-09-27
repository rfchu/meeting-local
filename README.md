# 听记 · 本地会议工作台 v0.9

面向普通话会议的 macOS 应用。录音转写、文字校对和导出均在本机完成；会议分析可选择本机模型，也可按需使用云端 OpenAI 兼容接口。

## 启动

这台 Mac 上已准备好 Python 环境、识别引擎和模型。在终端执行：

```bash
cd /Users/crf/Documents/Codex/2026-09-26/new-chat/outputs/meeting-local
./start.command
```

也可在 Finder 中双击 `start.command`。浏览器打开 <http://127.0.0.1:8765>。保持终端窗口运行；按 Ctrl+C 停止服务。已有服务时无需重复启动。

首次在其他 Apple Silicon Mac 上安装：

```bash
# 需要 Python 3.13（本机已验证）、Git、Xcode/命令行开发工具、FFmpeg
bash scripts/setup.sh
./start.command
```

安装阶段需要联网下载依赖和模型。使用本地分析模型时可离线运行；选择云端分析模型时，文字稿会发送到所配置的服务。应用默认只监听 `127.0.0.1`。

## 使用流程

1. 导入 WAV、MP3、M4A、FLAC、AAC、OGG，或带音轨的 MP4/MOV/WebM。单文件最大 4GB。
2. 选择“Whisper / 千问”识别引擎，按需要开启“自动区分发言人”。已知实际发言人数时指定人数，通常比自动估计更容易得到合理结果。
3. 点击“开始转写”。后台依次完成音频标准化、语音检测、分段识别、标点恢复和发言人分析。
4. 点击时间戳回听；文字和发言人名称可直接编辑，停止输入约 0.7 秒后自动保存。关闭页面前应确认显示“已保存”。
5. “管理发言人”用于按片段数查看、批量改名或合并发言人；“合并连续短句”可减少同一发言人的碎片片段。逐段编辑名称仍可纠正个别归属。
6. 可搜索替换、从版本历史恢复文稿，用“对比版本”查看两个引擎的结果，并导出 TXT、Markdown、SRT 或 JSON。
7. 在“LLM 模型配置”中添加本地 LM Studio 或云端 OpenAI 兼容服务，测试连接后，在“会议分析”页选择配置并生成结果。
8. 在“会议纪要”页选择正式版、精简版、内部工作版或 AI 会议纪要。AI 纪要可选“快速生成”（直接整理全文）或“完整处理”（先生成会议分析，再自动整理纪要）。生成后仍可修改负责人、截止时间和执行状态，通过导出预览检查版式，并导出 Word、PDF 或 Markdown。

失败或取消任务后，可从已完成片段的检查点重试。应用正常停止或意外退出后，尚未完成的运行任务会在下次启动时恢复排队。

重新转写会创建新版本；已有版本仍保留。如果任务运行期间发生了人工校对，模型结果保存为候选稿，不覆盖校对内容，可在会议概览中下载候选 JSON。

## 已实现

- 本地导入、长音频分段、Silero 语音检测。
- 双引擎：whisper.cpp / Metal + Whisper large-v3-turbo，以及 MLX + Qwen3-ASR-1.7B 8-bit。
- 千问使用 Qwen3-ForcedAligner-0.6B 8-bit 字词对齐，保留原生标点；历史版本显示引擎来源并支持左右对比。
- 千问片段触及输出长度上限时，会在靠近中点的低能量位置自动拆分并重试，再对原片段整体做时间戳对齐。
- CT-Transformer 中文/英文标点恢复，不允许标点模型改写实质文字。
- sherpa-onnx + pyannote segmentation / 3D-Speaker ERes2Net 发言人分段与聚类。
- 持久化单任务队列、取消/重试、检查点恢复、单工作进程锁。
- 原始音频 SHA-256、原始模型输出、不可变文稿版本、乐观并发检查。
- 播放定位、变速、搜索替换、自动保存、改名/合并发言人、导出。
- 后续摘要/决策/待办的类型约定、原文引用校验和“文稿更新后结果过期”机制。
- 本地/云端 OpenAI 兼容模型配置、连接测试、默认配置切换，以及长文稿分层归并、检查点恢复、可点击原文引用和 Markdown/JSON 导出。
- 四种平行的可编辑会议纪要：正式版突出决策与责任，精简版限制为一页关键信息，内部工作版保留完整依据，AI 会议纪要直接基于完整文字稿生成对外成稿；支持增删条目、行动项负责人/截止时间/状态、引用显示开关和 Markdown/Word 导出。
- AI 会议纪要使用所选本地或云端模型生成“会议基本信息、核心内容纪要、待办事项（Action Items）、会议总结”，并将所有事实性条目的引用限制在当前文字稿片段内；产物记录文字稿版本、模型和配置来源。
- 统一的四步处理进度与下一步入口；文字稿开始修改后立即提示分析和纪要将过期，保存新版本后保留更新提醒。
- 应用内发言人批量管理、同名合并和连续短句合并；会议纪要支持只读导出预览及原生 Word/PDF 输出。

## 当前边界

- 不包含实时字幕、录音采集和会议机器人。本地会议分析需要单独启动 LM Studio 并加载模型；云端服务会产生联网传输和可能的 API 费用。
- 默认引擎为 Whisper，可按会议选择千问。尚未用人工标注的真实会议集比较两者准确率。
- 发言人标签是聚类结果，不识别真实姓名；自动人数可能偏多，多人重叠、短句和远场拾音仍需人工校对。
- 时间戳为模型估计。一个片段可能含两个人的声音，目前按主要重叠时间分配发言人，不做同时说话的声音分离。
- Whisper 每 5 分钟、千问每 2 分钟分块，两侧各带 2 秒上下文，结合时间与文字去重合并。不确定的重叠会保留并标记“核对”，边界仍需要真实长会议验证。
- 标点按片段恢复，片段边界的语义连贯性需要校对。
- 目前没有 120 分钟真实会议、多人并发或正式 CER/DER 验收结果。短音频耗时不能直接外推为长会议吞吐量。
- 前端首版使用本地静态 HTML/CSS/JavaScript，无 CDN 或 npm 运行依赖。HTTP API 与前端解耦，后续可迁移到 React/TypeScript。

## 数据与架构

```text
app/main.py           本机 API、输入校验、导入/版本/导出
app/db.py             SQLite schema v3、事务、文稿修订与 LLM 配置
app/worker.py         持久化任务调度、检查点、进程管理
app/providers.py      识别/标点/发言人适配器
app/analysis.py       OpenAI 兼容模型适配、摘要分析与引用校验
app/minutes.py        会议纪要编排、引用校验与 Markdown/Word 生成
app/static/           本地网页界面
scripts/diarize.py    独立 ONNX 发言人进程
scripts/punctuate.py  独立标点进程
scripts/qwen_transcribe.py  独立 MLX 千问识别与时间戳对齐进程
models/               本地模型文件
vendor/whisper.cpp/   固定提交的推理引擎源码与构建
```

SQLite 与录音默认保存在 `data/`。LLM API Key 保存于本机 SQLite，接口和页面只返回“是否已配置”，不会回显密钥；数据库文件权限设为仅当前用户可读写。每场会议拥有独立目录：`original` 为原始文件，`asset.json` 记录文件哈希；任务子目录保存转换音频、模型原始 JSON、检查点和日志。模型缓存与会议数据分开。备份前停止应用，再复制整个 `data/` 目录；删除会议会删除关联音频、版本和任务。

环境变量：

- `MEETING_DATA`：数据目录。
- `PORT`：启动脚本使用的端口，默认 8765。
- `WHISPER_CLI` / `WHISPER_MODEL`：识别引擎和模型路径。
- `QWEN_PYTHON` / `QWEN_ASR_MODEL` / `QWEN_ALIGNER_MODEL`：千问独立环境、识别模型与对齐模型路径。
- `DIARIZATION_SEGMENTATION` / `DIARIZATION_EMBEDDING`：发言人模型路径。

转写任务记录引擎/模型文件身份；文件变更后不复用旧检查点，需要新建任务。推理子进程串行执行，完成后释放模型占用。

会议分析读取固定文稿版本，返回结构化条目与 `source_segment_ids`。分析模型与转写模型共用重型任务串行队列；分析结果绑定输入版本，当前文稿变化时自动标记过期。配置与使用方法见 [ANALYSIS.md](ANALYSIS.md)。

## 验证

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
node --check app/static/app.js
```

详细实际验证记录见 `VALIDATION.md`。千问安装、模型配置与版本对比说明见 [DUAL-ENGINE.md](DUAL-ENGINE.md)。

在新电脑上安装千问：`bash scripts/setup_qwen.sh`。

## 模型来源

- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)，当前源码提交 `d09f61a708f3487afa956ff578e60eae5e7a233c`。
- [Whisper GGML 权重](https://huggingface.co/ggerganov/whisper.cpp)。
- [Silero VAD 权重](https://huggingface.co/ggml-org/whisper-vad)。
- [sherpa-onnx 发言人模型文档](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html)。
- [sherpa-onnx 标点模型文档](https://k2-fsa.github.io/sherpa/onnx/punctuation/pretrained_models.html)。

下载的组件保留其原始许可证。对外分发应用及模型前，需要分别核对代码、模型权重和测试音频的使用条款。
