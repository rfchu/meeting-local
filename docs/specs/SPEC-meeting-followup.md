# 跟进与跨会议检索

模块 `meeting-followup`。P0。覆盖 PRD P0-09、10、11。依赖 `minutes-review` 与 `chinese-record`。见[共用规格](SPEC-workflow-common.md)。

用户明确确认行动项后进入跟进，可按项目或系列查状态、负责人、日期和来源。下次会议带入未完成事项。中文检索使用中文原文及有效译文，结果注明来源语言和译文身份。同一段原文与译文命中时合并成一个可定位的结果。引用问答沿用证据预览，以实际原文作为可核验引文。

| ID | 操作与异常 | 验收 |
| --- | --- | --- |
| MF-01 | 重复确认同一行动项，更新状态并开始系列下一场 | 只有一条有效跟进，变更持久保存，下一场看到未完成事项及原会议来源 |
| MF-02 | 中文查询只在英文发言中出现的约定 | 译文命中可查看并跳到原文和时间戳。默认不含个人备注，同源结果不重复 |
| MF-03 | 修改原文、撤下译文或打开旧会议 | 过期译文退出当前检索范围。旧记录原文仍可按已有方式查找。历史待办的来源状态可解释 |

复用 `app/meeting_flow.py`、`app/retrieval.py`、`app/qa.py`。基线命令 `.venv/bin/python -m pytest tests/test_handoff_followup.py tests/test_retrieval.py tests/test_retrieval_languages.py tests/test_qa.py -q`。
