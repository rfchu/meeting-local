# 实施计划：听记全产品 UI 架构与体验结构性重构

**规格地图**：[`docs/specs/MAP-ui-architecture-redesign.md`](../docs/specs/MAP-ui-architecture-redesign.md)  
**共用架构与边界**：[`docs/specs/SPEC-ui-common.md`](../docs/specs/SPEC-ui-common.md)  
**实施策略**：遵循纵向切片与增量交付，严格按 `design-tokens` → `app-shell` → `meeting-header-workflow` → `workspace-views` 四阶段推进；每完成一个阶段即执行静态检查、构建与 Headless Chrome CDP 实机多视口截图验收，验收确认后再开启下一阶段。

---

## 一、 核心架构与设计决策 (Architecture Decisions)

1. **零外链本地资产（WKWebView 安全合规）**：
   - `native/Host/WorkspacePolicy.swift` 屏蔽所有非 `127.0.0.1` 请求。新设计令牌（`tokens.css`）与单色线性 SVG 图标库（`icons.tsx`）全量通过 Vite 打包进 `app/static/`，零第三方 CSS/字体/路由运行时依赖。
2. **互斥状态机取代平级布尔弹窗（消灭双弹窗叠加）**：
   - 抽取 `frontend/src/shell/useAppShell.ts`，将主画布位置定义成互斥 `ShellRoute`（`meeting` | `library` | `followups` | `management` | `recording`），叠加层定义成互斥 `TransientModal`（`search` | `rename` | `retranscribe` | `prepare` | `notes` | `preview` | `compare` | `null`），并保持 `#management/tasks`、`#management/recovery`、`#companion-recording=*` 深链与 `browserWorkspace` 策略 100% 向后兼容。
3. **统一壳层与防误触录音锁定**：
   - 「会议资料库」「跨会议跟进」「运行与模型管理」保留左侧边栏，以全幅主画布呈现，统一入口命名为「运行与模型管理」；
   - 「会前准备」尚未开始录音前保留左侧导航以便随时返回；点击「开始实时录音」进入专注录音态后锁定主导航防止误中断；
   - 底部 `Player` Dock 仅在激活带源音频的 `meeting` 路由时渲染。
4. **首屏空间解放与默认阅读焦点**：
   - 会议详情头与 `Workflow` 压缩至双行紧凑条（总高 ≤ `160px`），核对清单与录音回执转为按需 Popover；
   - 「智能纪要」默认折叠左侧「原文与回听」分栏（突出正文阅读），通过顶栏 `[对照原文]` 开关一键展开；合并重复 Stale 提示并隐藏已完成态 `深度研判` 进度条盒。

---

## 二、 分阶段任务编排与验收检查点 (Phased Roadmap)

```text
阶段 1: design-tokens（样式地基与图标统一）
  ├── Task 1.1: 建立 tokens.css 与字阶/圆角/控件高度收口 (S)
  ├── Task 1.2: 构建本地单色线性 SVG 图标库 icons.tsx 并替换主工作区 Emoji (M)
  └── Task 1.3: 替换运行管理与会前准备页功能 Emoji (S)
  ──▶ [检查点 1: DS-01~DS-04 校验 + 批次 1 实机截图验收]

阶段 2: app-shell（统一应用外壳与互斥路由状态机）
  ├── Task 2.1: 实现互斥路由状态机 useAppShell 与深链单测 (M)
  ├── Task 2.2: 「会议资料库」「跨会议跟进」升格为主画布全幅视图 (M)
  └── Task 2.3: 统一「运行与模型管理」/「会前准备」壳层与播放器显隐边界 (M)
  ──▶ [检查点 2: SHELL-01~SHELL-05 校验 + 批次 2 实机截图验收]

阶段 3: meeting-header-workflow（会议工作区头部瘦身与工作流收敛）
  ├── Task 3.1: 合并紧凑双行会议头与行内 Workflow Popover (M)
  └── Task 3.2: 合并重复 Stale 版本警告条并补充回归单测 (S)
  ──▶ [检查点 3: HDR-01~HDR-04 校验 + 批次 3 实机截图验收]

阶段 4: workspace-views（子工作区排版精修与状态扫描性）
  ├── Task 4.1: 「智能纪要」分栏开关内联 & 「深度研判」对齐/进度条隐藏 (M)
  ├── Task 4.2: 「精细校对」版本控件一致性与次级提示行收敛 (S)
  └── Task 4.3: 「会前准备」800px 零裁切适配与侧边栏会议状态语义点 (M)
  ──▶ [检查点 4: VIEW-01~VIEW-05 校验 + 全视口截图终验 + 全量构建/宿主/后端回归]
```

详细可执行任务条目、验收标准与验证命令均记录于 [`tasks/todo.md`](todo.md)。

---

## 三、 风险与缓解措施 (Risks & Mitigations)

| 风险 | 影响 | 缓解策略 |
| :--- | :--- | :--- |
| `style.css`（6,680 行）全局选择器级联冲突 | 高 | 阶段 1 先仅向 `:root` 注入语义 Token 并精准覆写控件基准规则，不盲删历史选择器；每次改动后用 CDP 实测全 7 页快照 |
| `useAppShell` 抽取打断既有录音保活或深链跳转 | 高 | 先行编写 `appShell.test.mjs` 覆盖 `#management/*`、`#companion-recording=*` 与录音锁态，再渐进迁移 `App.tsx` 状态 |
| `Workflow` 改为 Popover 后影响键盘无障碍或现有自动化测试 | 中 | 保留 `aria-expanded` 与原有 `workflow-review-toggle` 等关键语义标记，确保 `handoff.test.mjs` 等全绿 |
| `RecordingSetupView` 小屏压缩影响波形与试音区可读性 | 中 | 在 `1180×800` 与 `900×600` 双尺寸下做 CDP 实测包围盒校验，优先优化冗余留白而非挤压波形交互区 |
