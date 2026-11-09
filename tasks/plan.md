# 实施计划：会中实时智能伴随工作台 (Foresight In-Meeting Copilot)

**规格参考**：[`docs/specs/PRD-foresight-in-meeting-copilot.md`](file:///Users/crf/Works/meeting-local/docs/specs/PRD-foresight-in-meeting-copilot.md)  
**基线版本**：`80ceb9c`  
**实施策略**：遵循纵向切片 (Vertical Slicing) 与测试驱动开发 (TDD)，每个切片均具备完整可验证性。

---

## 一、 架构与设计决策 (Architecture Decisions)

1. **速记触发与算力调度隔离**：
   * 采用显式双键模式：普通 `Enter` 保证 0 延迟秒级落盘为纯文本原始笔记；`⌘ + Enter` 异步调用端侧小模型进行上下文双向对齐与扩写。
   * 扩写推理走 `app/resources.py` 的调度队列，若此时 ASR 或正式纪要正在重度占用 Metal 显存，扩写以低优先级非阻塞排队，绝不中断正在进行的双轨音频录制与实时识别流。
2. **上下文对齐机制 (Temporal Anchoring)**：
   * 用户敲下速记时立即获取当前录音流的确切秒级时间戳 `T`。
   * 扩写提取窗口为 $[T - 30s, T + 10s]$ 的已确认和临时双轨转写分段，并注入当前的会议摘要/议题背景，提示词采用极简约束的 JSON 格式输出，强制包含发言人引用和原文时间区间。
3. **原生伴随轻岛 (macOS AppKit Native)**：
   * 复用现有 `native/Host` 中的 `NSPanel` 与 `ScreenCaptureKit` 桥梁，注册全局热键 `⌃ + ⌥ + N` (`NSEvent.addGlobalMonitorForEvents` 或 Carbon RegisterEventHotKey)。
   * 轻岛窗口设置为 `NSWindow.Level.floating` 且 `NSWindow.StyleMask.nonactivatingPanel`，呼出时不抢夺视频会议软件（Zoom/Tencent Meeting）的主焦点。
4. **前端架构解耦先行 (Pay Down Tech Debt)**：
   * 在引入统一三栏时间线前，优先对 `App.tsx` 中的弹窗管理与录音状态进行解耦，抽取独立的 `useMeetingPlayback` 与 `CopilotModals`，防止 2,040 行上帝组件进一步膨胀。

---

## 二、 任务拆解与分期计划 (Phased Task Plan)

```
阶段 1: 端侧速记扩写引擎 (Shorthand Engine)
  ├── Task 1.1: 数据模型与速记存储适配 (S)
  ├── Task 1.2: 上下文对齐与端侧小模型扩写服务 (M)
  └── Task 1.3: 速记 HTTP API 与 SSE 事件推送 (S)
  ──▶ [检查点 1: 后端单测与流式响应全绿]

阶段 2: 原生伴随轻岛 (Interactive Companion Island)
  ├── Task 2.1: 原生全局热键 ⌃+⌥+N 与轻岛悬浮窗 (M)
  ├── Task 2.2: 轻岛极简输入框与 Enter / ⌘+Enter 交互桥接 (M)
  └── Task 2.3: 动态微型双轨声波指示与状态胶囊 (S)
  ──▶ [检查点 2: 原生轻岛快捷呼出与双模速记走通]

阶段 3: 前瞻预测感知与决策捕获 (Speculative Intent)
  ├── Task 3.1: 会中流式语义意图探测器 (M)
  └── Task 3.2: 意图前瞻候选与一键采纳 API (S)
  ──▶ [检查点 3: 模拟会议流自动产出非阻塞决策/待办 Chip]

阶段 4: 前端三栏流式主时间线重构 (Unified Timeline UI)
  ├── Task 4.1: 解耦 App.tsx 弹窗与播放器状态 (M)
  ├── Task 4.2: 沉浸式三栏时间线视窗组件开发 (M)
  └── Task 4.3: 快捷键联动 (Tab 采纳 / Esc 忽略) 与端到端回归验收 (M)
  ──▶ [检查点 4: 全功能闭环与 919+ 测试无回归]
```

详细任务清单已同步记录在 [`tasks/todo.md`](file:///Users/crf/Works/meeting-local/tasks/todo.md)。