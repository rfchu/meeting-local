# 听记全流程开发任务

状态为分步实施中。条目中的命令用于实施后的验证。每项检查正常入口和异常路径，修改前端时另运行类型检查。见[实施计划](plan.md)和[共用规格](../specs/SPEC-workflow-common.md)。

## T01 按场景开始会议

- [x] 实施 T01

场景入口复用现有音源选择、试录和会议准备。

依赖 —。规格验收 MI-01。预计 4 个文件。

**验收与验证**

- [x] 线上与混合入口显示实际音源，导入显示文件来源。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_recording_consumers.py -q`。
- [x] 操作验证。打开三种入口，核对选项及返回位置。

可能涉及：`frontend/src/features/notes/RecordingView.tsx`、`frontend/src/features/meeting/MeetingBriefForm.tsx`、`frontend/src/App.tsx`、`tests/test_recording_consumers.py`。

提交 `384142e`。隔离页面核对线上／混合选择、实际音源和返回入口，合成文件通过导入 API 后仍能读取文件名。定向回归 3 项，前端 178 项及构建通过。

## T02 保存后进入对应会议

- [x] 实施 T02

串联保存回执与同场整理入口。

依赖 T01。规格验收 MI-02。预计 4 个文件。

**验收与验证**

- [x] 同次录音重复结束只创建一场会议，失败后可从恢复入口继续。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_recording_completion.py tests/test_meeting_handoff.py -q`。
- [x] 操作验证。保存、刷新及故障恢复后核对会议归属。

可能涉及：`app/recording_completion.py`、`app/meeting_handoff.py`、`frontend/src/features/notes/useRecordingController.ts`、`tests/test_recording_completion.py`。

提交 `fa61d32`。合成录音结束后进入同一会议，刷新恢复。并发重复提交、任务失败和重试由定向回归覆盖。后端 30 项，前端 178 项及构建通过。

## T03 查看待识别与音频缺失

- [x] 实施 T03

在整理界面消费现有覆盖与任务状态。

依赖 T02。规格验收 MI-03。预计 4 个文件。

**验收与验证**

- [x] 有音频的待识别范围与实际缺段分别呈现，重试不重复发布。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_meeting_handoff.py tests/test_recording_faults.py -q`。
- [x] 操作验证。注入缺段和识别故障，核对两类区间。
- [ ] 实际设备回听随 T16 统一记录。

可能涉及：`app/meeting_handoff.py`、`app/live_results.py`、`frontend/src/App.tsx`、`tests/test_meeting_handoff.py`。

提交 `92a66e9`。隔离页面同时显示 10 秒实时识别覆盖、20 秒未覆盖及一条已确认缺音。后端 39 项，前端 178 项及构建通过。

### T03 后的检查点

- [x] 三类入口与保存恢复已有功能验证记录，见 T01–T03。
- [x] 全量后端 865 项、前端 178 项及生产构建通过。
- [x] 已记录本轮验证，下一项为 T04 查看单段中文译文。
- [ ] T16 独立核对真实设备和可辨识声音。

## T04 查看单段中文译文

- [x] 实施 T04

用现有模型配置走通一段已确认英文的翻译及回听，先验证语言识别。

依赖 T03。规格验收 CR-01。预计 5 个文件。

**验收与验证**

- [x] 中文直接显示原文，英文原译关联同一时间范围，未知语言明确待确认。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_language_policy.py tests/test_live_session.py -q`。
- [x] 操作验证。用中英固定样本切换原文与中文视图。

可能涉及：`app/languages.py`、`app/routes/recordings.py`、`frontend/src/features/notes/RecordingView.tsx`、`frontend/src/api.ts`、`tests/test_language_policy.py`。

提交 `5175d87`。定向后端 31 项、资源调度 22 项、前端 178 项及构建通过。隔离录音页面验证中英样例切换，00:03 的英文片段与译文位置对应。回听入口由会后记录提供，真实设备回听在 T16 记录。


## T05 补译失败片段

- [x] 实施 T05

绑定原文版本与任务输入，支持失败范围重试。

依赖 T04。规格验收 CR-02、CR-03。预计 4 个文件。

**验收与验证**

- [x] 失败保留原文，重复请求复用结果，修改后迟到旧结果无法发布为当前译文。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_live_session.py tests/test_language_policy.py -q`。
- [x] 故障验证。用模型替身中断调用、改变来源并核对迟到保护。

可能涉及：`app/live_results.py`、`app/routes/recordings.py`、`frontend/src/features/notes/useRecordingController.ts`、`tests/test_live_session.py`。

提交 `b92f64d`。定向后端 24 项、前端 178 项及构建通过。模型替身验证超时、成功重试复用、模型或原文变化，以及迟到结果拒绝发布。取消等待会中止界面等待。


## T06 查看会后中文记录

- [x] 实施 T06

为阅读和后续模块提供有效译文、原文与缺口。

依赖 T05。规格验收 CR-04。预计 4 个文件。

**验收与验证**

- [x] 旧会议可读，部分翻译显示缺口，用户可补译或继续整理可用范围。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_language_policy.py tests/test_meeting_handoff.py -q`。
- [x] 操作验证。打开部分译文和旧会议，补译后核对计数。

可能涉及：`app/meeting_handoff.py`、`app/routes/meetings.py`、`frontend/src/App.tsx`、`tests/test_meeting_handoff.py`。

提交 `e0b9a37`。定向后端 73 项，首版检查点全量后端 871 项、前端 178 项及构建通过。隔离页面验证部分译文、补译后缺口计数更新及旧会议读取。中文记录接口提供可用内容，纪要生成消费中文记录由 T07 接入。

### T06 后的检查点

- [x] 中英混说、补译、迟到保护和缺译处理均有隔离验收记录。
- [x] 全量后端 871 项、前端 178 项及生产构建通过。
- [x] 已记录本轮验证。下一项为 T07 生成四类纪要草稿。
- [ ] 真实模型翻译质量及实际设备回听单列证据。

## T07 生成四类纪要草稿

- [x] 实施 T07

按会议类型使用已有模板和可用中文记录。

依赖 T06。规格验收 MR-01。预计 5 个文件。

**验收与验证**

- [x] 四类重点可辨，缺依据字段标为待确认，面试评语由人工填写。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_minutes_composition.py tests/test_interview.py -q`。
- [x] 操作验证。逐一检查四类合成会议。

可能涉及：`app/minutes_templates.py`、`app/minutes_jobs.py`、`frontend/src/features/minutes/MinutesView.tsx`、`tests/test_minutes_composition.py`、`tests/test_interview.py`。

提交 `e2001fa`。46 项定向测试通过，前端构建通过；四类合成草稿覆盖原文、可用译文和缺译回退。

## T08 从纪要回听原话

- [x] 实施 T08

为纪要条目关联实际来源并展示有效译文。

依赖 T07。规格验收 MR-02。预计 4 个文件。

**验收与验证**

- [x] 点击关键条目可回到原文时间戳，无依据的条目标为待核对。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_handoff_sources.py tests/test_minutes_jobs.py -q`。
- [x] 操作验证。在中英会议草稿中逐条打开决定与待办的来源。

可能涉及：`app/handoff_sources.py`、`app/minutes_jobs.py`、`frontend/src/features/minutes/MinutesView.tsx`、`tests/test_handoff_sources.py`。

提交 `257c50a`。45 项定向测试及前端构建通过；确认引用译文与历史原文同源，已移除的原文无回听跳转。T09 检查点在隔离页面点击决定与待办，确认双语证据及 00:01 回听定位。

## T09 保留人工稿并比较重生成

- [x] 实施 T09

绑定来源版本并复用草稿、采用和差异查看。

依赖 T08。规格验收 MR-03、MR-04。预计 4 个文件。

**验收与验证**

- [x] 旧来源提示变化，重生成与失败重试均保留人工修改及旧采用稿。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_minutes_jobs.py tests/test_meeting_handoff.py -q`。
- [x] 操作验证。修改译文后触发重生成，分别打开当前和历史稿。

可能涉及：`app/minutes_jobs.py`、`app/minutes_versions.py`、`frontend/src/features/minutes/MinutesView.tsx`、`tests/test_minutes_jobs.py`。

### T09 后的检查点

- [x] 四种模板及人工稿保护通过，P0-05、06 有结果记录。
- [x] 执行共用规格中的适用检查，确认当前系统可以从正常入口运行。
- [x] 记录结果并评审后续任务。

提交 `08755f6`。后端全量 878 项、前端 178 项、原生宿主 87 项通过；格式检查和构建通过。合成数据验证补译在排队后与生成中均阻止旧任务发布，采用的人工稿保持，重生成产生可对比草稿。P0-05、06 在合成范围验收，真实音频回听证据留至 T16。下一项 T10 对外交付预览。

## T10 预览对外选入的内容

- [x] 实施 T10

按受众从采用稿投影内容，服务端校验对外候选。

依赖 T09。规格验收 AD-01。预计 4 个文件。

**验收与验证**

- [x] 默认空选。私人备注、内部评语和未确认承诺不能经字段或引用进入预览。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_delivery_readiness.py -q`。
- [x] 操作验证。用带内部标记的样本检查标题、正文和引用。

可能涉及：`app/minutes_delivery.py`、`app/delivery_readiness.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_readiness.py`。

提交 0fffeeb。20 项交付检查测试和前端构建通过。用合成标记核对候选、标题、正文与引用隔离，未选择时预览正文为空。

## T11 分别确认两个受众版本

- [x] 实施 T11

按受众和来源绑定确认，保存独立快照。

依赖 T10。规格验收 AD-02、AD-03。预计 4 个文件。

**验收与验证**

- [x] 迟到确认需重新预览，重复确认只有一份有效快照，历史交付可读。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_delivery_readiness.py tests/test_delivery_tasks.py -q`。
- [x] 操作验证。交错修改采用稿和确认，检查内部及对外历史。

可能涉及：`app/delivery_readiness.py`、`app/routes/minutes.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_readiness.py`。

提交 d5e8cdb。54 项定向测试和前端构建通过。通过并发请求、交错切换采用稿与确认、历史读取验证两个受众的快照独立。

## T12 导出已确认交付件

- [x] 实施 T12

沿用三种导出格式读取已确认快照，区分发送记录。

依赖 T11。规格验收 AD-01、AD-03。预计 4 个文件。

**验收与验证**

- [x] 三种文件与预览内容一致，失败可重试，导出与已发送状态分别显示。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_delivery_readiness.py tests/test_delivery_tasks.py -q`。
- [x] 操作验证。打开 Word、PDF、Markdown 并与预览对照。

可能涉及：`app/minutes_delivery.py`、`app/routes/minutes.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_tasks.py`。

### T12 后的检查点

- [x] 受众隔离、三格式导出及旧交付兼容通过，P0-07、08、11 有结果记录。
- [x] 执行共用规格中的适用检查，确认当前系统可以从正常入口运行。
- [x] 记录结果并评审后续任务。

提交 f841e80。全量后端 883 项通过，补充行动项措辞校验后定向 66 项通过，前端 178 项、原生宿主 87 项通过。格式检查、构建通过。隔离页面完成两种受众确认、下载和独立发送记录。实际读取六份 Word、PDF、Markdown，核对正文与内部标记，验证 PDF 导出失败后可重试，旧交付可读取。P0-07、08、11 交付部分已有合成验收证据。下一项 T13 跟进。

## T13 将确认的待办带到下一场

- [x] 实施 T13

复用待办绑定和系列关系，从已核对纪要加入跟进。

依赖 T09。规格验收 MF-01。预计 4 个文件。

**验收与验证**

- [x] 重复确认不重复建待办，状态与来源持久保存，下次会议可见未完成项。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_handoff_followup.py -q`。
- [x] 操作验证。确认、更新、刷新，再创建系列下一场。

可能涉及：`app/meeting_flow.py`、`app/routes/meetings.py`、`frontend/src/features/meeting/MeetingBriefForm.tsx`、`tests/test_handoff_followup.py`。

提交 9b297d3。24 项定向测试和前端构建通过。合成系列验证重复确认、完成状态持久化、跨两次带入后保留最初会议及纪要来源。

## T14 用中文找到外语发言

- [x] 实施 T14

收录有效译文并在搜索结果显示原文来源。

依赖 T06。规格验收 MF-02、MF-03。预计 4 个文件。

**验收与验证**

- [x] 中文能命中英文发言，同源合并，过期译文及个人备注按规则排除。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_retrieval.py tests/test_retrieval_languages.py -q`。
- [x] 操作验证。搜索后打开原文并回听，编辑原文后重新搜索。

可能涉及：`app/retrieval.py`、`app/routes/retrieval.py`、`frontend/src/features/SearchDialog.tsx`、`tests/test_retrieval_languages.py`。

提交 37dcbd4。35 项定向测试和前端构建通过。验证中文与英文同源合并、时间定位、个人备注默认排除、撤下译文或修改原文后旧结果失效。

## T15 在问答中核对译文证据

- [x] 实施 T15

复用引用预览，以有效译文辅助回答并核验原文。

依赖 T14。规格验收 MF-02、MF-03。预计 4 个文件。

**验收与验证**

- [x] 译文身份可辨，回答引文对应原文，来源变化后的历史证据状态可解释。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_qa.py tests/test_retrieval_languages.py -q`。
- [x] 操作验证。预览中文证据、生成并打开对应原话。

可能涉及：`app/qa.py`、`app/routes/retrieval.py`、`frontend/src/features/CrossMeetingQA.tsx`、`tests/test_qa.py`。

### T15 后的检查点

- [x] 待办接续、译文检索和引用问答通过，P0-09、10 有结果记录。
- [x] 执行共用规格中的适用检查，确认当前系统可以从正常入口运行。
- [x] 记录结果并评审后续任务。

提交 9df2212。合成模型与隔离页面验证中文关键词找到 00:12 英文发言、双语预览、英文原句引文和跳转。自动化验证拒绝用译文充当引文、区分译文与原文变更、阻止过期证据继续使用，并保留旧会议原文检索。定向 56 项、全量后端 888 项、前端 178 项、原生宿主 87 项，格式检查和构建通过。P0-09、10 有合成验收记录，实机与真实模型质量证据留至 T16。

## T16 核对实机音源与整条流程

- [x] 实施 T16

汇总端到端证据并独立记录实机回听。

依赖 T12、T13、T15。规格验收 MI-04、P0-01 至 P0-12。预计 2 个文件。

**验收与验证**

- [x] 逐项核对十二条产品验收。记录真实音源、异常、回听和保存结果，并标明未验证场景。
- [x] 定向回归 `.venv/bin/python -m pytest tests/test_recording_validation.py tests/test_recording_faults.py -q`。
- [x] 操作验证。使用可核对的测试声音走线上及混合流程，另走导入和交付闭环。

可能涉及：`docs/engineering/HARDWARE_ACCEPTANCE.md`、`tests/test_recording_validation.py`。

### T16 后的检查点

- [x] 复核全部 P0、全量回归和真实音源证据，提交首版验收记录供审核。
- [x] 执行共用规格中的适用检查，确认当前系统可以从正常入口运行。
- [x] 记录结果并评审后续任务。

提交 f5a8019。十二条 P0 已记录证据和范围。线上单轨、混合双轨均完成实机短测并保存，用户确认混合回听。实际录音经导入、本机 Whisper 转写和内外版交付。混合录音第 55 秒记录到设备变化，后续语音完整性待专项验证。后端 888 项、录音定向 34 项、前端 178 项、原生宿主 87 项通过，格式检查和构建通过。

## P1 · 扩展语言

### T17 验证一个候选语言

- [x] 实施 T17

为候选语言固定识别、译文及混说样本，记录模型和可用配置。

依赖 T16。预计涉及 3 个文件。

- [x] 验收。单语与混说均有可回听结果，分别记录误识别、缺译、姓名与日期核对及处理耗时；失败组合保持未开放。
- [x] 验证命令 `tests/test_language_policy.py tests/test_live_session.py`。
- [x] 操作验证。用实际模型跑合成语音，记录输入、模型版本和结果。

可能涉及 `app/languages.py`、`app/providers.py`、`tests/test_language_policy.py`。

提交 3fc2ca7。本机实测四种组合，单语姓名及日期正确；同块混说分别漏英语和日语，Qwen 英语对齐也偏离实际位置。已保留可重建的语音样本、哈希、耗时和结果。20 项定向回归通过，T18 等待候选通过。

### T18 按能力开放会议主语言

- [x] T18a 用本机 Qwen 验证日语主导草稿。多种合成声音可生成可读草稿，发现姓名和人数误识，时间按音频块标记。
- [x] T18b 在会后转写设置的「更多语言」开放「日语 · 实验性（会后）」。草稿可按块回听和导出纯文本，与正式文字稿分开保存。
- [x] T18c 会后双语草稿。已开放本地中文译文、失败重试及按块回听。
- [ ] T18c 中文检索与原文引用。样本仍有姓名、人数误识及块级时间范围，继续保持正式索引和引用隔离。
- [ ] T18d 独立验证会中识别与实时双语。通过后按引擎开放。

样本、误识和开放边界见 [日语主导样本](../../evaluation/baselines/ja-dominant-v1.json)。

## P1 · 会中概览回看

### T19 按议题回看会中概览

- [x] 实施 T19

复用已保存概览、时间戳及历史版本，提供按议题浏览。

依赖 T16。预计涉及 4 个文件。

- [x] 验收。早期议题可见，每个有效条目可跳到对应原文，新增概览不打断回看位置。
- [x] 验证命令 `tests/test_live_overview.py tests/test_overview_quality.py`。
- [x] 操作验证。在多议题样本中切到早期议题并回听。

可能涉及 `app/live_overview.py`、`frontend/src/features/overview/OverviewArchiveButton.tsx`、`frontend/src/features/overview/types.ts`、`tests/test_live_overview.py`。

提交 b8eb6e8。36 项定向回归及构建通过。多议题合成样本验证滚动历史满 12 版后仍可打开早期议题，并保留原文定位。

### T20 查看概览的更新与待复核状态

- [x] 实施 T20

区分最新、历史、失败与来源过期的概览条目。

依赖 T19。预计涉及 4 个文件。

- [x] 验收。更新失败保留上一版；来源变化仅标记相关条目；人工确认可追溯。
- [x] 验证命令 `tests/test_live_overview.py tests/test_overview_quality.py`。
- [x] 操作验证。刷新、模拟迟到响应并修改引用原文。

可能涉及 `app/live_overview.py`、`frontend/src/features/overview/LiveOverviewPanel.tsx`、`frontend/src/features/overview/OverviewArchiveButton.tsx`、`tests/test_live_overview.py`。

提交 b8cb2cd。37 项定向测试及构建通过。模拟修改单条引用后，历史版仅标记受影响依据，保留另一条的确认时间、状态和对应快照。

#### T20 后的检查点

- [x] 核对会中概览回看的功能、异常与兼容验收，记录未通过或待验证项。
- [x] 执行[共用规格](../specs/SPEC-workflow-common.md)的适用回归及构建，核对正常入口。

## P1 · 受众模板自定义

### T21 保存自定义受众模板

- [x] 实施 T21

允许调整预设栏目与顺序，保存具备版本的受众模板。

依赖 T16。预计涉及 4 个文件。

- [x] 验收。非法或重复栏目有清楚提示；模板编辑前后的版本可区分；内置默认仍可使用。
- [x] 验证命令 `tests/test_minutes_composition.py tests/test_delivery_readiness.py`。
- [x] 操作验证。分别保存内部和对外模板，刷新后核对栏目。

可能涉及 `app/minutes_templates.py`、`app/routes/minutes.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_readiness.py`。

提交 f344c37。49 项定向测试及构建通过。验证内部和对外独立保存、版本冲突、禁止非法及重复栏目，内置模板保持可读。

### T22 用自定义模板预览交付

- [x] 实施 T22

把模板投影接入同一份预览、确认和导出流程。

依赖 T21。预计涉及 4 个文件。

- [x] 验收。自定义栏目遵循对外条目限制；确认绑定模板版本；修改模板后历史快照仍可重导。
- [x] 验证命令 `tests/test_delivery_readiness.py tests/test_delivery_tasks.py`。
- [x] 操作验证。选用自定义模板，核对预览及三种格式。

可能涉及 `app/minutes_delivery.py`、`app/delivery_readiness.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_readiness.py`。

提交 2328f66。68 项定向测试及构建通过。自定义模板受受众栏目和分享约束，变更后旧预览拒绝确认，三种历史导出继续读取已保存版本。

#### T22 后的检查点

- [x] 核对受众模板自定义的功能、异常与兼容验收，记录未通过或待验证项。
- [x] 执行[共用规格](../specs/SPEC-workflow-common.md)的适用回归及构建，核对正常入口。

## P1 · 交付历史筛选

### T23 按受众和时间筛选交付历史

- [x] 实施 T23

沿用现有快照列表，提供受众及日期筛选。

依赖 T16。预计涉及 4 个文件。

- [x] 验收。组合条件返回对应结果，边界日期可验证，旧快照有明确可发现入口。
- [x] 验证命令 `tests/test_delivery_tasks.py tests/test_delivery_readiness.py`。
- [x] 操作验证。混合新旧交付件后按时间与受众筛选。

可能涉及 `app/minutes_delivery.py`、`app/routes/minutes.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_tasks.py`。

提交 0db7f3b。33 项后端定向测试、前端筛选边界及构建通过。旧交付件归入内部版，筛选日期按设备本地日历且含起止日。

### T24 按发送状态定位交付件

- [x] 实施 T24

增加未发送、已发送、未知等筛选并保留打开快照的路径。

依赖 T23。预计涉及 3 个文件。

- [x] 验收。导出不会被筛为已发送；空结果可清除筛选；筛选结果可打开并重复导出。
- [x] 验证命令 `tests/test_delivery_tasks.py tests/test_delivery_readiness.py`。
- [x] 操作验证。登记发送后切换筛选并重新导出对应版本。

可能涉及 `app/minutes_delivery.py`、`frontend/src/features/minutes/MinutesDeliveryPanel.tsx`、`tests/test_delivery_tasks.py`。

提交 5fa5c70。区分已标记发送、未标记及旧件未知。隔离浏览器验证保存自定义模板、确认、标记发送、组合筛选和重新导出。全量后端 895 项、前端 180 项、原生宿主 87 项、格式检查和构建通过。

#### T24 后的检查点

- [x] 核对交付历史筛选的功能、异常与兼容验收，记录未通过或待验证项。
- [x] 执行[共用规格](../specs/SPEC-workflow-common.md)的适用回归及构建，核对正常入口。

## P1 · 悬浮窗

首项输出供独立评审的场景、范围和验证结果。后续任务以该项决策通过为前提。

### T25 确定悬浮窗使用场景

- [ ] 实施 T25

比较会中看译文、看重点和只看状态的场景，确认显示内容与可见性规则。

依赖 T16。预计涉及 2 个文件。

- [ ] 验收。记录目标场景、屏幕共享可见性、快捷入口及前后台焦点的验收环境。
- [ ] 验证命令 `bash scripts/test_host.sh`。
- [ ] 操作验证。用现有宿主核对前台会议软件、外接屏和共享时的窗口行为。

可能涉及 `native/Host/WorkspaceController.swift`、`docs/engineering/HARDWARE_ACCEPTANCE.md`。

阶段结果：内置屏合成窗口及 Lark 前台打开焦点检查通过，原生测试 87 项通过。Sidecar 双屏和断开回屏已复测通过。有线屏待测，Lark 接收端暂缓。详见 [T25 悬浮窗场景评审](../engineering/HARDWARE_ACCEPTANCE.md#t25-悬浮窗场景评审)。

### T26 打开只读伴随窗口

- [x] 实施 T26

在同一宿主和会话上显示已选文字或要点及录音状态。

依赖 T25。预计涉及 3 个文件。

- [ ] 验收。切换或关闭悬浮窗时录音继续按原状态运行；无第二份录音会话；窗口不会抢输入焦点。
- [x] 验证命令 `bash scripts/test_host.sh`。
- [ ] 操作验证。在录音期间切换工作台、悬浮窗与菜单栏。

可能涉及 `native/Host/WorkspaceController.swift`、`native/Host/AppDelegate.swift`、`native/HostTests/main.swift`。

已验证内置屏打开不转移焦点、合成会话只读及三种显示模式；真实音轨切换与关闭待实测。

### T27 从悬浮提示返回对应会议

- [x] 实施 T27

用已有状态和定位入口处理断线、过期及操作提示。

依赖 T26。预计涉及 3 个文件。

- [ ] 验收。断线显示最近状态时间，重连不启动采集，异常入口定位正确会议。
- [x] 验证命令 `bash scripts/test_host.sh`。
- [ ] 操作验证。断开本机服务再恢复，验证提示和跳转。

可能涉及 `native/Host/ServiceClient.swift`、`native/Host/WorkspaceController.swift`、`native/HostTests/main.swift`。

隔离服务断开及重启通过，工作台定向页面验证通过；悬浮窗点击至工作台的完整人工链路待补验。

#### T27 后的检查点

- [x] 记录悬浮窗的功能、异常与兼容结果；Sidecar 已通过，Lark 接收端暂缓。
- [ ] 执行[共用规格](../specs/SPEC-workflow-common.md)的适用回归及构建，核对正常入口。

## P1 · 工作台与伴随窗体验

### UX01 收敛工作台布局

已实现全宽正文及常驻标记入口（285aa41）。构建及 180 项回归通过，隔离浏览器已核对录音工作台入口。真实录音中的宽窄屏首屏和切换仍待操作验收。

- [ ] 盘点会中和会后任务。在窄屏和桌面窗口用同一场测试会议记录当前首屏、操作步骤和滚动位置。
- [ ] 将采集状态和主动作收进紧凑工具栏。简记、诊断和配置按需展开，切换面板保留当前阅读位置。
- [ ] 验收。会中默认首屏同时可见录音状态、主要动作和正文。会后切换文字与纪要时保留正在播放的位置。

### UX02 优化会中概览、纪要和文字记录

已实现概览快捷来源、纪要回听入口、原文和双语播放高亮及折叠校对工具（766e917、7007fd6）。构建及 180 项回归通过，隔离浏览器使用合成原声验证时间戳回听与高亮。完整纪要和会中概览操作链路待复验。

- [ ] 概览以当前议题、已确认结论和下一步为主要内容。历史议题和复核提示放在次级区域。
- [ ] 文字记录采用稳定的时间、发言人、正文层级。原文和译文同段对齐，播放时高亮对应段落，允许暂停跟随。
- [ ] 验收。从一条纪要到对应原文和录音可直接定位，返回后仍保留阅读位置。核对失败和缺译能在对应位置看见。

### UX03 优化只读伴随窗

已实现原译文分层、重点列表、内容自适应高度和返回本场入口（f90d52e）。94 项原生回归及宿主构建通过，真实录音中的悬浮窗操作待验。

- [x] 保持默认录音状态，优化双语、重点和返回本场入口。正文优先展示最近一段话或当前议题下的关键条目。
- [ ] 验收。录音期间切换模式、关闭再打开、返回本场，录音继续且不会多开会话。核对焦点与 Sidecar 断开后的可见区域。Lark 接收端不作为本轮门槛。

录音可靠性按实际问题定向跟进。T31 保留为发生设备问题时的策略评审项，当前无排期。已移出开发清单的任务编号保持空缺。
