# 项目文档

| 我想了解 | 文档 |
| --- | --- |
| 安装后如何录音、校对、交付和检索 | [使用指南](USER_GUIDE.md) |
| 项目当前进度与后续方向 | [项目路线图](PROJECT_ROADMAP.md) |
| 产品范围、流程和验收要求 | [产品需求](specs/PRD-meeting-workflow.md) |
| 独立 macOS App、模型包与发行验收 | [独立发行能力地图及模块规格](specs/MAP-standalone-distribution.md) · [技术方案](planning/standalone-release-plan.md) · [任务清单](planning/standalone-release-tasks.md) |
| 活跃工作、模块依赖和验收状态 | [实施计划](planning/plan.md) · [任务清单](planning/todo.md) |
| 架构、构建和回归测试 | [开发与验证](engineering/DEVELOPMENT.md) |
| 目录、备份恢复、桌面升级和回退 | [本机维护](engineering/WORKSPACE.md) |
| 原生录音控制及实机验证 | [采集协议](engineering/CAPTURE_PROTOCOL.md) · [硬件验收](engineering/HARDWARE_ACCEPTANCE.md) |
| 历史技术决策与已交付批次 | [工程演进与历史验收](engineering/DELIVERY_HISTORY.md) |
| 合成语料评测与本机样本处理 | [固定质量评测](evaluation/README.md) · [私有样本流程](evaluation/PRIVATE_SAMPLES.md) |

命令默认从仓库根目录执行。维护中的产品规格放在 `specs/`，排期和验收清单放在 `planning/`。本机草稿、真实会议和私有验收材料通过 Git 忽略规则隔离。
