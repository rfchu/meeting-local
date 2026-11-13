# 任务清单与进度跟踪 (Todo)

**实施计划**：[`tasks/plan.md`](plan.md)  
**规格地图**：[`docs/specs/MAP-ui-architecture-redesign.md`](../docs/specs/MAP-ui-architecture-redesign.md)

---

## 阶段 1：设计系统与样式基座 (`design-tokens`)

### Task 1.1: 建立核心 Design Tokens 与排版/控件基准收口
**Description:** 新建 `frontend/src/styles/tokens.css` 并在 `main.tsx` 最顶部引入，确立表面色、语义状态色、5 级字阶（≥12px）、4 级圆角（`6px/8px/12px/9999px`）、3 档控件高度（`28px/34px/40px`）与统一 `z-index` 层级，收口 `style.css` 基础按钮/输入/标签的离散圆角与微字号，降低嵌套边框视觉噪音。

**Acceptance criteria:**
- [x] `frontend/src/styles/tokens.css` 定义完整色彩、圆角、字阶、高度与层级变量并在 `main.tsx` 首行引入。
- [x] 全站常规按钮、输入框、选择器圆角收敛至规范集（消除 `0px/4px` 等杂散圆角），业务文本与标签字号 `≥ 12px`（品牌微标除外）。
- [x] 基础构建与类型检查通过。

**Verification:**
- [ ] Tests pass: `npm --prefix frontend run typecheck && npm --prefix frontend run test:stream`
- [ ] Build succeeds: `npm --prefix frontend run build -- --outDir /private/tmp/tingji-ui-build --emptyOutDir`
- [ ] Manual check: CDP 采样 `borderRadius` 与 `fontSize` 确认杂散值清零

**Dependencies:** None  
**Files likely touched:**
- `frontend/src/styles/tokens.css` (新增)
- `frontend/src/main.tsx`
- `frontend/src/style.css`

**Estimated scope:** S (3 files)

---

### Task 1.2: 构建本地单色线性 SVG 图标库并替换主工作区 Emoji
**Description:** 新建 `frontend/src/styles/icons.tsx` 导出统一 `16px/18px`（1.5px 描边）本地 React SVG 图标组件；替换 `Sidebar.tsx`、`App.tsx`、`Workflow.tsx` 与 `TranscriptView.tsx` 中充当功能图标的彩色 Emoji（`🔍 ☑ 📁 📋 📌 💡 📄 🌐 🛠️`）及部分生硬符号，保证 100% 离线可用。

**Acceptance criteria:**
- [x] `icons.tsx` 提供本地内联 SVG 组件，零外部 URL 引用。
- [x] 侧边栏导航、工作流提示条、备会/速记触发器、精细校对工具条不再使用彩色系统 Emoji。
- [x] `ActionMenu` 与现有按钮的可访问标签 `aria-label` 与文本提示完整保留。

**Verification:**
- [ ] Tests pass: `npm --prefix frontend run typecheck && npm --prefix frontend run format:check`
- [ ] Build succeeds: `npm --prefix frontend run build -- --outDir /private/tmp/tingji-ui-build --emptyOutDir`
- [ ] Manual check: 确认静态产物无任何外部请求

**Dependencies:** Task 1.1  
**Files likely touched:**
- `frontend/src/styles/icons.tsx` (新增)
- `frontend/src/components/Sidebar.tsx`
- `frontend/src/components/Workflow.tsx`
- `frontend/src/features/transcript/TranscriptView.tsx`
- `frontend/src/App.tsx`

**Estimated scope:** M (5 files)

---

### Task 1.3: 替换运行管理与会前准备页功能 Emoji
**Description:** 将 `RuntimeManagement.tsx`、`RuntimeOverviewPage.tsx` 与 `RecordingSetupView.tsx` 中的 3D/彩色 Emoji（`📊 ⏳ 🛡️ 🧠 🎛️ 🔍 ⚙️ 🎙️ ⚡ ☕ 📝 🤖`）统一切换为 `icons.tsx` 单色线性图标，对齐整体专业视觉语言。

**Acceptance criteria:**
- [x] 控制中心左侧导航与运行总览指标卡采用统一线性 SVG 图标。
- [x] 会前准备与就绪舱三大配置区块标题采用统一线性 SVG 图标。
- [x] 页面交互与样式在 `1440×813` 下居中对齐、无图标挤压。

**Verification:**
- [ ] Tests pass: `npm --prefix frontend run typecheck && npm --prefix frontend run test:stream`
- [ ] Build succeeds: `npm --prefix frontend run build -- --outDir /private/tmp/tingji-ui-build --emptyOutDir`
- [ ] Manual check: 检查运行总览与准备页图标渲染

**Dependencies:** Task 1.2  
**Files likely touched:**
- `frontend/src/features/management/RuntimeManagement.tsx`
- `frontend/src/features/management/RuntimeOverviewPage.tsx`
- `frontend/src/features/notes/RecordingSetupView.tsx`

**Estimated scope:** S (3 files)

---

### 检查点 1：批次 1（`design-tokens`）实机验收
- [x] 执行全量静态与宿主验证：`npm --prefix frontend run typecheck && npm --prefix frontend run format:check && npm --prefix frontend run test:stream && bash scripts/test_host.sh`
- [x] 通过 Headless Chrome CDP 采集核心界面截图与 DOM 指标，核对 `DS-01` ~ `DS-04`
- [x] **与用户截图验收确认后再进入阶段 2**

---

## 阶段 2：统一应用外壳与路由状态机 (`app-shell`)

### Task 2.1: 抽取互斥顶层路由状态机 `useAppShell` 及其单元测试
**Description:** 新建 `frontend/src/shell/useAppShell.ts` 实现互斥 `ShellRoute`（`meeting` | `library` | `followups` | `management` | `recording`）与互斥 `TransientModal`，内置 `#management/tasks`、`#management/recovery` 与 `#companion-recording=*` Hash 深链解析，及「录音进行中锁定切页」逻辑；编写纯 Node 单元测试并在 `App.tsx` 接入该状态机替代分散布尔开关。

**Acceptance criteria:**
- [x] 任意切换 `ShellRoute` 自动清零当前打开的 `TransientModal`；打开新弹窗自动替换旧弹窗，互斥保证 ≤ 1 个弹窗。
- [x] 完整支持 `#management/tasks`、`#management/recovery`、`#companion-recording=<32hex>` 与 `browserWorkspace` 约束。
- [x] `frontend/tests/appShell.test.mjs` 覆盖所有状态转换、录音锁态与深链场景并全绿。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck && node --test frontend/tests/appShell.test.mjs`（注：`test:stream` 仅 `tests/meetingView.test.mjs` 存在 3 项既定历史失败，其余 187/190 全绿）
- [x] Build succeeds: `npm --prefix frontend run build`
- [x] Manual check: 验证深链 URL 直达与多弹窗切换互斥

**Dependencies:** Checkpoint 1  
**Files likely touched:**
- `frontend/src/shell/shellState.ts` (新增)
- `frontend/src/shell/useAppShell.ts` (新增)
- `frontend/tests/appShell.test.mjs` (新增)
- `frontend/src/App.tsx`

**Estimated scope:** M (3 files)

---

### Task 2.2: 将「会议资料库」与「跨会议跟进」升格为主画布全幅视图
**Description:** 解除 `MeetingLibrary.tsx` 与 `FollowupsDialog.tsx` 对 `<Modal>` 遮罩弹窗的依赖，改为渲染在 `<main>` 主工作画布内的全幅视图（支持页面标题头与筛选区铺开）；保留左侧持久化 `Sidebar` 并高亮当前选中导航项，彻底消灭双弹窗叠罗汉。

**Acceptance criteria:**
- [x] 「会议资料库」与「跨会议跟进」在主工作区全幅展示，无半透明遮罩与弹窗居中限制。
- [x] 侧边栏「会议资料库」「跨会议跟进」与「最近会议」之间可零阻碍自由互切，打开任意会议直接进入详情工作区。
- [x] 原有归类、筛选、归档、批量删除、待办状态勾选与保存逻辑完整无损。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck && node --test frontend/tests/appShell.test.mjs`
- [x] Build succeeds: `npm --prefix frontend run build`
- [x] Manual check: 资料库直切跨会议跟进再按 `⌘K` 打开搜索，确认同时 `.modal-backdrop <= 1`

**Dependencies:** Task 2.1  
**Files likely touched:**
- `frontend/src/features/meeting/MeetingLibrary.tsx`
- `frontend/src/features/meeting/FollowupsDialog.tsx`
- `frontend/src/App.tsx`
- `frontend/src/style.css`

**Estimated scope:** M (4 files)

---

### Task 2.3: 统一「运行与模型管理」及「会前准备」壳层导航与播放器边界
**Description:** 将 `RuntimeManagement` 嵌入统一主外壳右侧画布（保留左侧全局导航或同步高亮控制中心，统一入口文案为「运行与模型管理」）；让「会前准备」未开录前可保留侧边栏导航、开始实时录音后进入沉浸锁定态；收紧底栏 `Player` 渲染条件，仅在存在源音频的 `meeting` 路由下贴底显示。

**Acceptance criteria:**
- [x] 侧边栏、分析页配置链接与会前准备页底部均统一显示「运行与模型管理」。
- [x] 在「资料库」「跨会议跟进」「运行与模型管理」页面底部不渲染 `Player` 播放条，切回有音频的会议后立即恢复。
- [x] 会前准备态允许从侧边栏返回任意历史会议；正式进入实时录音流态后锁定防误触跳转。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck && node --test frontend/tests/appShell.test.mjs && bash scripts/test_host.sh`
- [x] Build succeeds: `npm --prefix frontend run build`
- [x] Manual check: 分别验证运行管理页、资料库页与会前准备页的底部及左侧导航行为

**Dependencies:** Task 2.2  
**Files likely touched:**
- `frontend/src/components/Sidebar.tsx`
- `frontend/src/components/AppModals.tsx`
- `frontend/src/features/management/RuntimeManagement.tsx`
- `frontend/src/features/notes/RecordingSetupView.tsx`
- `frontend/src/App.tsx`

**Estimated scope:** M (5 files)

---

### 检查点 2：批次 2（`app-shell`）实机验收
- [x] 执行校验：`npm --prefix frontend run typecheck && npm --prefix frontend run format:check && node --test frontend/tests/appShell.test.mjs && bash scripts/test_host.sh`（`test:stream` 187/190 通过，3 项为历史既定 `tests/meetingView.test.mjs` 失败）
- [x] 通过 Headless Chrome CDP 实拍资料库、跟进、运行管理及互切路径截图，验证 `SHELL-01` ~ `SHELL-05`
- [x] **与用户截图验收确认后再进入阶段 3**

---

## 阶段 3：会议工作区头部瘦身与工作流收敛 (`meeting-header-workflow`)

### Task 3.1: 实现紧凑双行会议头与工作流 Popover 浮层化
**Description:** 精简 `App.tsx` 会议详情顶部（取消单独占行的面包屑，日期并入副信息行），重组为「标题+操作行」与「元数据+工作流胶囊行」两层；重构 `Workflow.tsx`，将常驻占高的双层 `.workflow` 提示盒压缩为行内胶囊触发器，点击 `交接与核对 · X/Y` 或 `识别覆盖/录音异常` 时以轻浮层展开完整核对清单与 `RecordingReceipt`。

**Acceptance criteria:**
- [x] `1440×813` 与 `1180×800` 下 `#meeting-workspace` 以上全部头部与工作流条带总高度 `≤ 160px`（CDP 实测 `wsTop: 106px`，`headerHeight: 76px`）。
- [x] 核心内容区首屏可视占比 `≥ 72%`（CDP 实测 `shareFromWsTop: 0.87 / 0.868`，扣除 `81px` 播放器后仍达 `0.77 / 0.766`）。
- [x] 工作流核对弹出浮层包含全部人工核对按钮、定位跳转与关闭复原功能，关闭后不占用垂直高度（CDP 实测 `POPOVER_SHIFT_PX: 0`，`POPOVER_CLOSED: true`）。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck` 通过；目标模块测试 `30/30` 通过（`test:stream` 188/191 通过，仅 `tests/meetingView.test.mjs` 存在 3 条历史默认页签断言失败）
- [x] Build succeeds: `npm --prefix frontend run build` 通过（已构建至 `app/static/assets/`）
- [x] Manual check: CDP `getBoundingClientRect()` 实测头部高度 ≤ 160px 与 Popover 交互

**Dependencies:** Checkpoint 2  
**Files likely touched:**
- `frontend/src/App.tsx`
- `frontend/src/components/Workflow.tsx`
- `frontend/src/style.css`

**Estimated scope:** M (3 files)

---

### Task 3.2: 合并重复的来源变更（Stale）黄色警告条
**Description:** 梳理 `MinutesView.tsx` 与 `AnalysisView.tsx` 中围绕「来源变更」与「文稿版本落后（v1 vs v2）」的重复黄色告警逻辑，抽取合并规则将多条高度重叠提示整合为单条紧凑警告行，并内联一键 `[用 vN 重新生成]` 操作按钮，同时补充/更新工作流单测验证。

**Acceptance criteria:**
- [x] 纪要或研判落后于当前文稿版本时，首屏最多展示 **1 条**紧凑黄色提示条，包含差异说明与直达重生成按钮（CDP 实测 `智能纪要` 与 `深度研判` 均为 `noticeCount: 1`，按钮文案 `用 v2 重新生成`）。
- [x] 不丢失旧版只读回看与版本对比功能入口（`版本历史与对比`、取消/失败重试及只读查看仍完整保留）。
- [x] 现有与新增单元测试全部通过（`minutesWorkflow.test.mjs` 4/4 通过，含 `resolveMinutesStaleNotice` 与 `resolveAnalysisStaleNotice` 合并规则测试）。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck` 通过；`minutesWorkflow.test.mjs` 4/4 通过（全量 `test:stream` 188/191，3 条为历史 `meetingView.test.mjs` 失败）
- [x] Build succeeds: `npm --prefix frontend run build` 通过（已构建至 `app/static/assets/`）
- [x] Manual check: 检查带 stale 状态会议的「智能纪要」首屏正文直接露出

**Dependencies:** Task 3.1  
**Files likely touched:**
- `frontend/src/features/minutes/MinutesView.tsx`
- `frontend/src/features/analysis/AnalysisView.tsx`
- `frontend/tests/minutesWorkflow.test.mjs`
- `frontend/src/style.css`

**Estimated scope:** S (4 files)

---

### 检查点 3：批次 3（`meeting-header-workflow`）实机验收
- [x] 执行校验：`npm --prefix frontend run typecheck && npm --prefix frontend run format:check` 全部通过；目标模块单测 `30/30` 通过（`test:stream` 188/191，仅 3 条历史 `meetingView.test.mjs` 失败）；`bash scripts/test_host.sh` `105/105` 通过
- [x] 通过 Headless Chrome CDP 实测头部像素高度、视口占比及智能纪要/深度研判首屏截图，验证 `HDR-01` ~ `HDR-04`
- [x] **与用户截图验收确认后再进入阶段 4**

---

## 阶段 4：子工作区排版与状态体验精修 (`workspace-views`)

### Task 4.1: 精修「智能纪要」分栏开关内联与「深度研判」对齐及完成态收敛
**Description:** 调整 `App.tsx`（`reviewSourceOpen` 默认 `false` 突出主文档，消除单独成行的 `[收起原文]` 空白行，将分栏切换按键整合入 `MinutesView.tsx` 顶栏工具区，收紧左右分栏多层套框）；修复 `AnalysisView.tsx` 副标题左贴边无内边距缺陷，并在 `job.state === "completed"` 时隐藏 `#analysisJob` 静态满格进度盒。

**Acceptance criteria:**
- [x] 「智能纪要」首屏默认聚焦纪要文档正文，点击工具区 `[对照原文]` 可平滑展开/收起左侧原文与回听分栏，左右顶栏齐平，嵌套边框 `≤ 2` 层（CDP 实测默认 `sourcePaneOpen: false`，`hasLegacyToggle: false`，分栏展开后 `topAlignedDiff: 0`）。
- [x] 「深度研判」副标题与工具栏左侧像素级对齐（CDP 实测 `alignDiffPx: 0`），分析完成态不残留静态进度条占用高度（`hasAnalysisJobCard: false`）。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck` 通过；定向单测 `31/31` 通过（全量 `test:stream` 189/192，仅 `tests/meetingView.test.mjs` 存在 3 条历史默认页签断言失败）
- [x] Build succeeds: `npm --prefix frontend run build` 通过（已构建至 `app/static/`）
- [x] Manual check: CDP 对比智能纪要单/双栏与深度研判完成态截图

**Dependencies:** Checkpoint 3  
**Files likely touched:**
- `frontend/src/App.tsx`
- `frontend/src/features/minutes/MinutesView.tsx`
- `frontend/src/features/analysis/AnalysisView.tsx`
- `frontend/src/style.css`

**Estimated scope:** M (4 files)

---

### Task 4.2: 统一「精细校对」版本选择控件规格并压缩辅助信息栏
**Description:** 在 `TranscriptView.tsx` 中用规格一致的 `ActionMenu` 或自定义触发器替换原生 `版本历史 (vN)` `<select>`，使其与旁边的 `批量校对工具` 保持一致的 `34px` 高度与 `8px` 圆角；将 `已启用大文稿流畅渲染` 提示合并精简为单行元信息，释放校对区垂直阅读空间。

**Acceptance criteria:**
- [x] 顶部 Pro Toolbar 中 `批量校对工具` 与 `版本历史` 按钮实测高度差 `≤ 1px`、圆角一致（CDP 实测 `34px` vs `34px`，`heightDiff: 0`，圆角同为 `8px`）。
- [x] 保留所有历史版本切换、导出与大文稿虚拟渲染开关功能（合并至单行 `27px` 状态信息条 `共 362 个片段 · 已启用大文稿流畅渲染` + `展开全部 DOM (⌘F)`）。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck` 通过；定向单测 `31/31` 通过
- [x] Build succeeds: `npm --prefix frontend run build` 通过（已构建至 `app/static/`）
- [x] Manual check: CDP 实测两个触发按钮 `getBoundingClientRect().height` 一致性

**Dependencies:** Task 4.1  
**Files likely touched:**
- `frontend/src/features/transcript/TranscriptView.tsx`
- `frontend/src/components/ActionMenu.tsx`
- `frontend/src/style.css`

**Estimated scope:** S (3 files)

---

### Task 4.3: 修复「会前准备」小屏截断与升级侧边栏列表语义状态点
**Description:** 优化 `RecordingSetupView.tsx` 与 `recordingWorkbench.css` 的双栏容器弹性高度与区块间距，移除与主界重复的冗长底部静态状态条，确保 `1180×800` 及 `1440×813` 下右侧 `会后整理目标` 等表单控件零截断；在 `Sidebar.tsx` 为最近会议列表增加 `6px` 语义状态指示点（失败红色、处理中蓝色、待处理琥珀色）。

**Acceptance criteria:**
- [x] 在 `1180×800` 与 `1440×813` 视口下，「会前准备与就绪舱」所有配置控件及末尾 `会后整理目标` 完全可见，无底部裁剪（CDP 实测 `subBottom: 730px ≤ 800px / 813px`，`fitsWithoutPageScroll: true`；`900×600` 下 `rootOverflow: false` 且可滚动）。
- [x] 侧边栏中 `failed`、`running` 会议条目具有鲜明语义状态视觉点（CDP 实测 `6×6` 语义圆点，新增 `appShell.test.mjs` 单测校验）。

**Verification:**
- [x] Tests pass: `npm --prefix frontend run typecheck` 通过；`appShell.test.mjs` 7/7 通过
- [x] Build succeeds: `npm --prefix frontend run build` 通过（已构建至 `app/static/`）
- [x] Manual check: CDP 多分辨率（`1440×813`、`1180×800`、`900×600`）快照检验零裁剪

**Dependencies:** Task 4.1  
**Files likely touched:**
- `frontend/src/features/notes/RecordingSetupView.tsx`
- `frontend/src/features/notes/recordingWorkbench.css`
- `frontend/src/components/Sidebar.tsx`
- `frontend/src/style.css`

**Estimated scope:** M (4 files)

---

### 检查点 4：批次 4（`workspace-views`）终验与全量发布就绪
- [x] 正式构建前端产物至 `app/static/`：`npm --prefix frontend run build`
- [x] 执行全量回归验证：
  - `npm --prefix frontend run typecheck`（通过）
  - `npm --prefix frontend run format:check`（通过）
  - `npm --prefix frontend run test:stream`（189/192 通过，仅 3 条 `tests/meetingView.test.mjs` 历史默认页签断言失败）
  - `bash scripts/test_host.sh`（105/105 通过）
  - `PYTHONPATH=. .venv/bin/python -m pytest tests/test_backups.py -q`（22/22 通过；直接 `.venv/bin/python -m pytest -q` 1029 passed，1 failed 为无 `PYTHONPATH=.` 导致的 `test_backups.py` `No module named 'app'`）
- [x] 通过 Headless Chrome CDP 重跑全视图与多分辨率快照并检验 `VIEW-01` ~ `VIEW-05`
- [x] **全部验收通过后提交并与用户汇报确认**
