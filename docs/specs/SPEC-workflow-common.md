# 全流程模块共用规格

适用于已确认 PRD 的五个模块。逐模块规格、实施计划和任务均供审核。

沿用仓库锁定的 React／TypeScript、FastAPI、SQLite 和 Swift 原生采集。业务和接口分别在 `app/`、`app/routes/`，界面在 `frontend/src/features/`，测试在 `tests/` 和 `frontend/tests/`。新增字段遵循已有 API 命名，界面类型明确表达状态，格式化沿用 Prettier。现有风格示例取自 `app/languages.py`：

```python
def configure_asr(provider, language):
    provider.language = source_language(language)
    return provider
```

命令从仓库根目录执行。模块规格和任务另列定向回归。

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run test:stream
npm --prefix frontend run format:check
npm --prefix frontend run build -- --outDir /private/tmp/tingji-workflow-build --emptyOutDir
bash scripts/test_host.sh
```

- 始终验证正常、失败、重试及旧记录路径。用合成输入和模型替身验证规则，用隔离页面验证正常入口，用本机模型验证真实调用，用实际回听验收设备采集。
- 计划评审明确数据迁移、新依赖和模型、云端发送范围。异步任务沿用现有资源调度、取消、检查点及本机／云端配置。
- 禁止无授权发送内容，禁止提交真实会议材料或凭据。原音、原文、人工修改和已确认交付件需要保留。译文、AI 草稿及人工确认保持各自的来源和版本。
- 完成条件包括定向测试、相关构建和实际入口验证。记录环境、样本和结果，分别标注合成与实机证据。
