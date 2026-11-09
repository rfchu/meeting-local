# 任务清单与进度跟踪 (Todo)

**实施计划参考**：[`tasks/plan.md`](file:///Users/crf/Works/meeting-local/tasks/plan.md)  
**规格文档**：[`docs/specs/PRD-foresight-in-meeting-copilot.md`](file:///Users/crf/Works/meeting-local/docs/specs/PRD-foresight-in-meeting-copilot.md)

---

## 阶段 1：端侧速记扩写引擎 (Shorthand Engine)

### Task 1.1: 数据模型与速记存储适配
**Description:** 在现有 `meeting_notes` 表结构与 `RecordingStore` 中扩展速记字段，增加 `raw_text`、`synthesized_text`、`anchor_start`、`anchor_end`、`speaker_hint` 和 `status`（`raw` / `synthesizing` / `ready` / `failed`），保证纯文本笔记即时落盘与后续增量扩写更新。

**Acceptance criteria:**
- [x] 扩展会议笔记存储模式，向下兼容现有笔记数据结构。
- [x] 保存普通速记时，`status='raw'` 且 `raw_text` 立即安全落盘，返回对应的 `note_id` 与精确时间戳。
- [x] 支持按 `note_id` 更新扩写内容与锚定音频区间。

**Verification:**
- [x] Tests pass: `PYTHONPATH=. .venv/bin/pytest -q tests/test_shorthand_storage.py`
- [x] Manual check: 在 SQLite 中直接插入带扩展字段的速记数据，读取完整无损。

**Dependencies:** None  
**Files likely touched:**
- `app/db.py`
- `app/notes.py`
- `app/recordings.py`
- `tests/test_shorthand_storage.py` (新增)

**Estimated scope:** S (2–3 files)

---

### Task 1.2: 上下文对齐与端侧小模型扩写服务
**Description:** 创建 `app/shorthand.py` 服务，根据速记打点时间戳 `T` 自动拉取前后窗口 $[T - 30s, T + 10s]$ 的实时转写记录（包含发言人与音轨），构造严格输出 JSON 的微型扩写 Prompt，通过本地 LLM Provider 进行异步扩写，提取结构化笔记与发言人引用。

**Acceptance criteria:**
- [x] 准确检索对应时间段的双轨转写片段，若录音刚开始不足 30 秒则自动截取从头开始。
- [x] 扩写输出结构符合格式契约（包含 `summary`、`details`、`speakers`、`evidence_quote`）。
- [x] 当本地模型推理超时（> 3 秒）或显存争抢时，自动降级保留 `raw_text` 并标记 `status='failed'`，绝不抛出未捕获异常。

**Verification:**
- [x] Tests pass: `PYTHONPATH=. .venv/bin/pytest -q tests/test_shorthand_synthesizer.py`
- [x] Manual check: 模拟一组转写片段与碎词输入（如 `db RC 压测周五`），校验扩写条目的准确性。

**Dependencies:** Task 1.1  
**Files likely touched:**
- `app/shorthand.py` (新增)
- `tests/test_shorthand_synthesizer.py` (新增)

**Estimated scope:** M (2–3 files)

---

### Task 1.3: 速记 HTTP API 与实时 SSE 事件推送
**Description:** 在后端暴露 `/api/recordings/{rid}/notes/shorthand` 接口（支持 `mode='raw'` 与 `mode='synthesize'`），并通过现有的 `live_session` SSE 广播通道（或独立事件流）向前端推送 `note.created` 与 `note.synthesized` 事件。

**Acceptance criteria:**
- [x] `POST /api/recordings/{rid}/notes/shorthand` 接收 `text` 与可选的 `synthesize=true` 标志。
- [x] 若 `synthesize=false`，接口在 50ms 内返回已持久化的 `note_id`。
- [x] 若 `synthesize=true`，后台异步派发扩写任务，完成后通过 SSE 实时向客户端广播新生成的扩写成果。

**Verification:**
- [x] Tests pass: `PYTHONPATH=. .venv/bin/pytest -q tests/test_shorthand_routes.py`
- [x] Build succeeds: `PYTHONPATH=. .venv/bin/pytest -q`

**Dependencies:** Task 1.1, Task 1.2  
**Files likely touched:**
- `app/routes/copilot.py` (新增)
- `app/live_session.py`
- `app/main.py`
- `tests/test_shorthand_routes.py` (新增)

**Estimated scope:** S (3–4 files)

---

## 检查点 1: 后端端侧速记引擎验证
- [x] 所有新增速记与上下文对齐单测全部通过 (11 passed)。
- [x] 原有 919 个自动化测试 100% 保持绿色通过，无回归 (930 passed)。
- [x] 验证模拟速记输入到落盘与 SSE 推送端到端畅通。

---

## 阶段 2：原生伴随轻岛 (Interactive Companion Island)

### Task 2.1: 原生全局热键 `⌃ + ⌥ + N` 与轻岛悬浮窗
**Description:** 改造 `native/Host/CompanionController.swift`，通过 AppKit 将其转变为非激活悬浮小岛（`NSPanel`，`floating` 层级，隐藏标准标题栏与红绿灯），并利用 macOS 全局快捷键监视机制注册 `⌃ + ⌥ + N` 快捷键，实现秒级平滑展开与收起。

**Acceptance criteria:**
- [x] 在系统任意窗口（全屏 Zoom、浏览器、桌面）按下 `⌃ + ⌥ + N`，小岛在屏幕右上角平滑滑出。
- [x] 呼出时不夺取前台其他全屏软件的活动焦点（Non-activating）。
- [x] 再次按下 `⌃ + ⌥ + N` 或轻按 `Esc`（当输入框为空时）平滑收起。

**Verification:**
- [x] Build succeeds: `bash scripts/build_host.sh`
- [x] Manual check: 在 macOS 桌面按下 `Control + Option + N` 测试小岛呼出与收起动效。

**Dependencies:** Checkpoint 1  
**Files likely touched:**
- `native/Host/CompanionController.swift`
- `native/Host/AppDelegate.swift`
- `native/Host/HostPreferences.swift`

**Estimated scope:** M (3 files)

---

### Task 2.2: 轻岛极简输入框与 Enter / ⌘+Enter 交互桥接
**Description:** 在伴随轻岛视图中嵌入极简原生输入行，接管键盘输入事件：普通 `Enter` 触发普通速记落盘；`⌘ + Enter` 触发智能扩写；通过本地回环 HTTP 客户端异步提交至后端。

**Acceptance criteria:**
- [x] 单行输入框获得光标焦点，输入流畅不丢字。
- [x] 按 `Enter`：输入框立即清空并提供微震动/微高亮反馈，后台发送 `mode=raw` 请求。
- [x] 按 `⌘ + Enter`：输入框清空，条目进入右侧/下方流并显示轻量 `[扩写中...]` 骨架占位，扩写返回后平滑展开。

**Verification:**
- [x] Build succeeds: `bash scripts/build_host.sh`
- [x] Manual check: 在原生轻岛中分别用 `Enter` 和 `⌘ + Enter` 输入两条测试笔记，核验数据库中落盘内容。

**Dependencies:** Task 2.1, Task 1.3  
**Files likely touched:**
- `native/Host/CompanionController.swift`
- `native/Host/ServiceClient.swift`

**Estimated scope:** M (2–3 files)

---

### Task 2.3: 动态微型双轨声波指示与状态胶囊
**Description:** 在轻岛头部整合微型双轨实时音量指示器（麦克风绿波、系统音频蓝波）与状态指示胶囊（录制时长、当前发言人或最新识别文字摘要），让用户无需切窗即可感知录音与识别状态。

**Acceptance criteria:**
- [x] 双轨有声时实时绘制平滑波形条，静音时柔和收拢。
- [x] 状态指示显示录制时长与 ASR 状态点（就绪、识别中、轻度延迟）。
- [x] 资源占用极低（UI 渲染刷新 CPU 增量 $\le 1.5\%$）。

**Verification:**
- [x] Build succeeds: `bash scripts/build_host.sh`
- [x] Manual check: 播放本地音乐并对着麦克风说话，观察伴随轻岛声波的动态分轨响应。

**Dependencies:** Task 2.1  
**Files likely touched:**
- `native/Host/CompanionController.swift`

**Estimated scope:** S (1–2 files)

---

## 检查点 2: 原生交互式伴随轻岛闭环
- [x] 原生宿主正常编译并通过构建。
- [x] 外部视频通话中随时按 `⌃ + ⌥ + N` 呼出轻岛，敲入笔记立即记录并能按需扩充。
- [x] 原生伴随窗与主程序录音状态完全同步。

---

## 阶段 3：前瞻预测感知与决策捕获 (Speculative Intent)

### Task 3.1: 会中流式语义意图探测器
**Description:** 创建 `app/speculative_intent.py`，作为 ASR 实时流的旁路订阅者，以滑动窗口（如最近 45 秒内新增的确认分段）检测高置信度共识、决议（Decisions）与承诺待办（Action Items），生成结构化候选对象。

**Acceptance criteria:**
- [x] 过滤低信息量闲聊，仅对包含明确动作主体、时间边界或共识动词（如“确定”、“按这个定”、“我周五前发”）的句子进行触发。
- [x] 意图探测运行在独立的后台线程/任务中，执行耗时设硬性上限（$\le 1.2s$）。
- [x] 产生候选时打上唯一 `candidate_id` 与置信度分数，避免相邻窗口重复推荐同一决议。

**Verification:**
- [x] Tests pass: `PYTHONPATH=. .venv/bin/pytest -q tests/test_speculative_intent.py`
- [x] Manual check: 注入标准会议用例对话流，检验决策候选与待办识别的准确度与去重逻辑。

**Dependencies:** Checkpoint 1  
**Files likely touched:**
- `app/speculative_intent.py` (新增)
- `app/live_session.py`
- `tests/test_speculative_intent.py` (新增)

**Estimated scope:** M (3 files)

---

### Task 3.2: 意图前瞻候选与一键采纳 API
**Description:** 在后端提供前瞻候选查询与采纳接口：`POST /api/recordings/{rid}/candidates/{cid}/accept`（将候选直接提升为正式会议待办/决策）与 `/reject`（忽略该候选），并通过 SSE 将新候选广播给伴随窗与前端。

**Acceptance criteria:**
- [x] 客户端可实时接收到 `candidate.detected` 事件。
- [x] 采纳接口原子性将条目写入会议待办/决策表，并返回最新列表。
- [x] 忽略接口将该条目记入会话抑制列表，本场不再弹出相同内容。

**Verification:**
- [x] Tests pass: `PYTHONPATH=. .venv/bin/pytest -q tests/test_copilot_candidates.py`

**Dependencies:** Task 3.1  
**Files likely touched:**
- `app/routes/copilot.py`
- `app/task_management.py`
- `tests/test_copilot_candidates.py` (新增)

**Estimated scope:** S (2–3 files)

---

## 检查点 3: 前瞻预测感知流验证
- [x] 意图探测与候选采纳单测全量通过。
- [x] 验证后台实时流在识别到“这个我周五交”后，能在 2 秒内发出待办候选事件。
- [x] 919+ 既有测试保持全绿。

---

## 阶段 4：前端三栏流式主时间线重构 (Unified Timeline UI)

### Task 4.1: 解耦 App.tsx 弹窗与播放器状态
**Description:** 治理历史技术债务：将 `App.tsx` 中的全局弹窗（`RenameDialog`, `SearchDialog`, `FollowupsDialog`, `TermDialog`, `SpeakerReviewDialog` 等）提取至 `frontend/src/components/AppModals.tsx`，将播放器时间更新隔离至 `useMeetingPlayback`，大幅压缩 `App.tsx` 行数与重渲染范围。

**Acceptance criteria:**
- [x] 抽离弹窗组件为 `AppModals.tsx` 并完成解耦重构。
- [x] 弹窗展现、关闭及播放器跳转功能 100% 保持原有行为一致。
- [x] 播放器播放时不再引起全应用无谓的大面积重新渲染。

**Verification:**
- [x] Typecheck: `cd frontend && npm run typecheck`
- [x] Build succeeds: `cd frontend && npm run build`
- [x] Manual check: 打开主界面核对各弹窗与播放控制，确保所有现有功能无缺失。

**Dependencies:** Checkpoint 1  
**Files likely touched:**
- `frontend/src/App.tsx`
- `frontend/src/components/AppModals.tsx` (新增)
- `frontend/src/hooks/useMeetingPlayback.ts` (新增)

**Estimated scope:** M (3–4 files)

---

### Task 4.2: 沉浸式三栏时间线视窗组件开发
**Description:** 在 `frontend/src/features/copilot/` 中开发 `UnifiedTimeline.tsx`，形成左侧导航/元信息、中间连续转写与速记流、右侧实时萃取看板的三栏连续视窗，彻底取代反复在“实时文字/简记/概览”间 Tab 跳切的操作方式。

**Acceptance criteria:**
- [x] 中间栏将双轨文字、用户原始笔记、AI 扩写卡片按时间戳融合在单条时间线上，支持平滑虚拟滚动。
- [x] 右侧看板直观聚合“已确认决策”、“待办事项清单”及“速记要点”。
- [x] 点击右侧任意条目，中间时间线自动平滑滚动并高亮对应的语音转写段。

**Verification:**
- [x] Typecheck: `cd frontend && npm run typecheck`
- [x] Build succeeds: `cd frontend && npm run build`
- [x] Manual check: 加载一段包含转写与笔记的录音，验证三栏时间线的数据展现与联动跳转。

**Dependencies:** Task 4.1, Task 1.3, Task 3.2  
**Files likely touched:**
- `frontend/src/features/copilot/UnifiedTimeline.tsx` (新增)
- `frontend/src/features/copilot/TimelineItem.tsx` (新增)
- `frontend/src/features/copilot/ExtractedSidebar.tsx` (新增)
- `frontend/src/App.tsx`

**Estimated scope:** M (4 files)

---

### Task 4.3: 快捷键联动 (Tab 采纳 / Esc 忽略) 与端到端回归验收
**Description:** 在主工作台及轻岛视窗中实现前瞻预测卡片的浮层动效与无障碍键盘快捷键操作：当有前瞻决策/待办浮出时，按 `Tab` 即可直接采纳沉淀，按 `Esc` 忽略，完成整个 Foresight Copilot 闭环验证。

**Acceptance criteria:**
- [x] 前瞻胶囊在时间线底部以微动效浮现，支持 `Tab` 键一键采纳、`Esc` 键快捷忽略。
- [x] 采纳后即刻在右侧看板中生成已确认条目，并反向在时间线上标记锚点。
- [x] 运行完整的端到端回归测试套件，验证项目整体稳定性。

**Verification:**
- [x] Backend tests: `PYTHONPATH=. .venv/bin/pytest -q` (940 passed, 100% 全绿)
- [x] Frontend build & check: `cd frontend && npm run typecheck && npm run build` (通过)
- [x] Host test: `bash scripts/test_host.sh` (99 passed)

**Dependencies:** Task 4.2  
**Files likely touched:**
- `frontend/src/features/copilot/SpeculativeChips.tsx` (新增)
- `frontend/src/features/copilot/UnifiedTimeline.tsx`
- `tests/test_copilot_e2e.py` (新增)

**Estimated scope:** M (3–4 files)

---

## 最终检查点: 全流程闭环验收
- [x] 原生伴随轻岛按 `⌃ + ⌥ + N` 瞬间唤出，输入框普通回车存纯文本，`⌘ + Enter` 智能扩充。
- [x] 会中前瞻预测卡随语音流平滑浮现，按 `Tab` 即可一键沉淀为待办/决策。
- [x] 主工作台三栏流式时间线阅读畅快，彻底告别频繁 Tab 切换。
- [x] 后端原有 919 个单测以及所有新增测试 100% 通过（共 940 个通过），端侧离线与隐私铁律保持完好。

---

## 阶段 5：产品化升级与架构技术债治理 (Productization & Technical Hardening)

### Task 5.1: 消除技术债务与测试阻塞 (Step 1)
- [x] 修复 `tests/test_module_boundaries.py` 中的 OpenAPI schema 契约哈希校验（适配 `NativeRecordingInput.microphone_only`）。
- [x] 在 `native/AudioCapture/main.swift` 中为纯麦克风路径补齐设备中断（`AVCaptureSessionWasInterruptedNotification`）与拔出监听。
- [x] 在 `app/native_capture.py` 中统一收敛 `native_tracks(session)`，消除内联特判。
- [x] 验证：全量 1018 个后端单测 100% 绿色通过，原生宿主测试全部通过。
- [x] 提交：Git commit Step 1。

### Task 5.2: 录音配置场景化降噪与移除多语言硬编码 (Step 2)
- [x] 重构 `RecordingSettingsDialog.tsx`：将技术性音源选择收敛为场景化卡片（“线上会议” vs “现场会议”）。
- [x] 将底层技术名词（“AEC3”、“常驻进程模型释放”等）收敛进高级折叠面板，普通视图保持极致清爽。
- [x] 清理全局针对特定语种的过渡性硬编码（移除 `japaneseBeta` 横幅、特化导出按钮等），统一纳入标准语言流。
- [x] 验证：`cd frontend && npm run typecheck && npm run build` 成功。
- [x] 提交：Git commit Step 2。

### Task 5.3: 导航与时间线视窗归一与浏览器端体验完善 (Step 3)
- [x] 将 Foresight 三栏统一时间线 (`UnifiedTimeline`) 设为已结束会议的默认呈现视图。
- [x] 收敛顶部过重的审核漏斗（`Workflow`），将【深入分析】与【会议成果】提升为平级一级导航，消除下拉菜单暗藏入口。
- [x] 浏览器工作台体验闭环：会话过期提供友好卡片引导，并提供原生客户端唤醒入口。
- [x] 最终全量验收与回归。
- [x] 提交：Git commit Step 3。