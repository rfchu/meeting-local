# 听记独立发行能力地图

能力边界已确认，逐模块规格待评审。独立版面向 Apple Silicon Mac，最低系统版本暂按 macOS 15。App 安装包、听记模型包和用户自行配置的 LLM 分开交付。

| 模块 | 职责 | 依赖 |
| --- | --- | --- |
| [app-runtime](SPEC-app-runtime.md) | 从 App 启动工作台、后台和采集组件，管理本机数据与生命周期 | 无 |
| [model-packs](SPEC-model-packs.md) | 下载或导入完整模型包，校验后交给识别等组件使用 | app-runtime |
| [llm-setup](SPEC-llm-setup.md) | 引导用户连接自管的本地或云端 LLM | app-runtime |
| [app-release](SPEC-app-release.md) | 签名与公证、安装升级、旧资料迁移及发行验收 | app-runtime、model-packs、llm-setup |

依赖顺序为独立运行底座 → 模型包与 LLM 引导 → 发行验收。模型包与 LLM 引导可分别验收。开发模式仍由源码版维护流程负责。

本轮确认需求与验收条件。安装包构建方案、模型包传输格式和任务拆分进入后续评审。
