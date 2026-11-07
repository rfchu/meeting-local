# 纪要生成与核对

模块 `minutes-review`。P0。覆盖 PRD P0-05、06、11。依赖 `meeting-intake` 与 `chinese-record`。见[共用规格](SPEC-workflow-common.md)。

项目推进、客户沟通、内部讨论及面试分别沿用 PRD 映射的模板。生成以有效中文记录、可核验原文、会前议程和明确选入的简记为输入。用户在同一工作区查看处理状态，核对决定、承诺、日期、负责人和未决问题，点击来源回听并可编辑采用。人工备注和模型生成项有清楚区分。若使用部分记录生成，草稿显示当时的覆盖范围。

| ID | 操作与异常 | 验收 |
| --- | --- | --- |
| MR-01 | 四类样本分别生成草稿 | 项目显示进度和风险，客户显示共识与承诺，内部讨论显示决定与未决项，面试区分陈述与人工评语。缺依据字段显示待确认 |
| MR-02 | 对照译文核对决定和待办 | 引用仍指原文 ID 与对应音频，译文作为辅助阅读。无有效来源的生成项标记待核对 |
| MR-03 | 编辑并采用后再生成，期间改变文字稿或译文 | 人工内容和既有采用稿可找回。新生成结果独立可比较。旧来源明确提示失效 |
| MR-04 | 生成中断、失败、翻译不完整或打开旧会议 | 可重试或手工整理，显示可用范围及缺口。旧版本仍可回看 |

复用 `app/minutes_templates.py`、`app/minutes_jobs.py`、`app/minutes_versions.py` 与 `frontend/src/features/minutes/`。基线命令 `.venv/bin/python -m pytest tests/test_minutes_jobs.py tests/test_minutes_composition.py tests/test_interview.py tests/test_handoff_sources.py -q`。
