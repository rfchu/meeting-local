# 千问 / Whisper 双引擎

这次扩展在现有转写、校对和版本系统上增加本地引擎选择。默认仍为 Whisper，用户可按会议切换为千问。

## 使用

1. 导入或打开会议。
2. 在“识别引擎”选择 **Whisper large-v3-turbo** 或 **千问 Qwen3-ASR-1.7B · 8-bit**。
3. 按需要设置自动发言人分析与人数，开始转写。
4. 对同一会议切换另一引擎并重新转写，会新增文稿版本；旧版本保留。
5. 点击 **对比版本**，左右选择两个历史版本，只读查看结果，不影响当前校对稿。版本菜单也会显示所用引擎。

当前选项控制下一次新建任务。取消后“从检查点重试”仍使用原任务的引擎、模型身份和分段参数，不会因下拉框改变而切换。未安装对应模型时禁止提交；千问失败也不会静默切换为 Whisper。

## 实现

| 项目 | Whisper | 千问 |
|---|---|---|
| 识别模型 | large-v3-turbo | Qwen3-ASR-1.7B，8-bit MLX 转换 |
| 本机后端 | whisper.cpp / Metal | mlx-audio / MLX GPU |
| 时间戳 | Whisper 句级预测 | Qwen3-ForcedAligner-0.6B，8-bit，字词对齐后组合为句 |
| 主分段长度 | 300 秒 | 120 秒 |
| 标点 | CT-Transformer 后处理 | 保留千问输出标点 |
| 发言人 | 共用现有 ONNX 管线 | 共用现有 ONNX 管线 |

千问 ASR 先输出文本，卸载 ASR 后加载对齐模型，用真实预测时间戳构建片段。单个识别片段前后保留 2 秒上下文，使用现有合并逻辑。对齐结果缺字、不匹配或时间越界会报错，原始文字与对齐结果留存在任务目录，避免伪造平均时间戳或静默漏掉文字。

如果一个片段达到 ASR 输出 token 上限，运行器会在片段中部附近寻找低能量位置，将其拆成更短的音频递归重试。成功后仍使用完整原片段和合并后的文字进行 ForcedAligner 对齐；`*.raw.json` 中的 `asr_attempts` 记录每次时长、token 数和是否触顶。

后端 `get_asr_provider` 根据任务的 `engine` 路由；旧任务没有此字段时按 Whisper 处理。SQLite 表结构不变，引擎、分段配置记录在任务 JSON；文稿版本记录具体 ASR 和对齐模型。

运行接口：

```json
POST /api/meetings/{meeting_id}/jobs
{"engine":"qwen","diarization":true,"num_speakers":4}
```

`GET /api/status` 返回两个引擎的就绪状态。既有 API 调用不传 `engine` 时仍为 Whisper。

## 安装与复现

千问使用独立环境 `.venv-qwen`，不会修改 Whisper 主环境的依赖。首次安装需要联网；正常推理只读取本地模型。

```bash
bash scripts/setup_qwen.sh
```

- 依赖锁定在 `requirements-qwen-lock.txt`。
- 模型仓库修订及文件校验值在 `qwen-models-manifest.json`；安装脚本优先使用已记录的修订。
- `QWEN_PYTHON`：独立 Python 路径，默认 `.venv-qwen/bin/python`。
- `QWEN_ASR_MODEL` / `QWEN_ALIGNER_MODEL`：本地模型目录。
- 推理环境设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，不请求云端 API。

## 效果评估边界

两个引擎的片段划分不同，版本对比显示各自原始句段，并非逐字差异对齐。千问更准确与否需要用同一批真实会议录音和人工标注比较；运行成功不等于已证实准确率更高。当前社区 MLX 8-bit 转换的效果也不能直接等同于官方原始权重基准。

更换 ASR 不改变声纹聚类模型，无法直接解决人数估计或多人重叠造成的说话人错误。

## 千问组件来源

- [Qwen3-ASR 官方项目](https://github.com/QwenLM/Qwen3-ASR)
- [MLX Audio](https://github.com/Blaizzy/mlx-audio)，本机锁定 0.5.6。
- [ASR 8-bit 转换权重](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit)
- [ForcedAligner 8-bit 转换权重](https://huggingface.co/mlx-community/Qwen3-ForcedAligner-0.6B-8bit)

运行 `python3 scripts/verify_models.py` 可核对已交付模型清单内的 SHA-256。
