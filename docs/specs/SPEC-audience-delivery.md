# 内部与对外交付

模块 `audience-delivery`。P0。覆盖 PRD P0-07、08、11。依赖 `minutes-review` 的采用稿及核对结果。见[共用规格](SPEC-workflow-common.md)。

从同一采用稿分别制作内部版和对外版。对外初始无选入条目，用户逐项确认可分享内容。私人备注、内部评语和未确认承诺不进入对外候选。标题、引用摘录、附加字段也属于最终预览范围。确认绑定受众、内容选择和来源版本，预览与导出读取同一份已确认内容。导出和用户记录的已发送状态分别显示。

| ID | 操作与异常 | 验收 |
| --- | --- | --- |
| AD-01 | 用同一采用稿制作两个受众版本 | 各自保存。对外导出只包含已选且允许分享的内容，Word、PDF、Markdown 和预览在内容上对应 |
| AD-02 | 预览后修改选项、来源或采用稿再提交 | 提示重新预览，旧预览不能确认成新交付件。已确认旧件仍可回看及重复导出 |
| AD-03 | 重复确认、导出失败、刷新或读取旧快照 | 同一确认请求不产生重复交付。导出失败可重试。导出不会把发送状态置为已发送，旧快照可读 |

复用 `app/minutes_delivery.py`、`app/delivery_readiness.py` 与 `frontend/src/features/minutes/MinutesDeliveryPanel.tsx`。基线命令 `.venv/bin/python -m pytest tests/test_delivery_readiness.py tests/test_delivery_tasks.py -q`。
