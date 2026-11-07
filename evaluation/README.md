# F1：固定评测集与质量基线

首批是自编合成样本与本机 TTS，不含真实会议、候选人或个人资料。固定样本和评分代码均入 Git；没有从候选模型输出反推参考答案，也不因某次结果改写真值。它建立可复现的回归入口，不是模型排行榜或真实会议准确率认证。

## 范围和已保存结果

| 类别 | 固定输入 | 指标／边界 |
| --- | --- | --- |
| 跨会议引用守卫 | 8 个正确／伪造引用、未知来源、个人备注和拒答样本 | 生产校验器的接纳／拒绝是否符合预期；引用存在不等于语义充分 |
| 纪要引用守卫 | 3 个正文及基本信息来源样本 | 真实来源 ID 约束；不评价全文事实覆盖或语言质量 |
| 弹性分段 | 4 个连续语音、静音、硬上限、停止尾段 PCM 样本 | 已确认与临时状态、边界及范围；不等于真实语音端点准确率 |
| 发言人评估器 | 1 个独立编写固定片段标签与包含误改的建议样本 | 确认纠正／伤害统计正确；不是实际模型建议质量，也不是 DER |
| 会中概览流程 | 冻结既有合成增量样例，共 7 轮 | 日期变更、提议与决定、引用、稳定条目和人工保留等流程约束 |
| ASR 字符质量 | 4 段固定普通话 TTS，共 122 个归一化参考字符 | CER 与每段输出；不包含真实噪声、重叠说话或长会议 |

`baselines/protocol-v1.json`：23/23 个离线探针通过，0 次模型调用。通过表示上述守卫／流程行为符合固定样例，不表示 AI 理解或纪要准确率为 100%。

`baselines/whisper-tts-v1.json`：本机 Whisper large-v3-turbo 实际执行四次生产 transcribe，6 个字符差异／122 个字符，micro CER **4.918%**，2 段归一化后完全相同。原始合成输出和逐段误差保留在报告，便于复核。三处差异来自中文数字与阿拉伯数字的书写形式；另有专名等差异。没有据此修改默认引擎或比较 Qwen 优劣。

F2 补充：既有 Qwen 文件已取回本机，同一四段输入和 F1 评分口径实测 0/122 字符差异，CER 0%；正式报告为 `baselines/qwen-tts-v1.json`。这个结果仅限小型合成集。真实片段的授权、人工确认、冻结版本和评分另见 [PRIVATE_SAMPLES.md](PRIVATE_SAMPLES.md)，不混入该总体分数。

F1 当轮 Qwen 未生成基线：权重和部分配置为 macOS 云端占位文件，首次请求在加载阶段无输出后被终止；不伪造或按零错误计分。评测预检及生产模型就绪检测现在用文件元数据拒绝占位文件，避免“文件存在”等同“本机可离线使用”。请先在 Finder 将所选模型／配置保持下载到本机，再显式运行 Qwen。此预检检查 provider 登记的文件，不代表整个 Python 环境的所有依赖都已驻留；本轮未执行模型下载或替换命令，新预检在加载前检查驻留。

## 使用

从项目根目录运行；输出必须是新文件，基线不会自动覆盖。`--fixtures` 是全局参数，需写在子命令前。

```sh
.venv/bin/python scripts/evaluate_quality.py validate
.venv/bin/python scripts/evaluate_quality.py offline --output /private/tmp/protocol-candidate.json
.venv/bin/python scripts/evaluate_quality.py compare --baseline evaluation/baselines/protocol-v1.json --candidate /private/tmp/protocol-candidate.json --output /private/tmp/protocol-comparison.json
```

只有显式标记才运行本机模型；正式录音／任务繁忙时拒绝开始，并在每条请求前复查，不停止正式工作。它是独立顺序评测进程，不声称已加入生产跨进程资源调度；任务开始后，当前已提交请求最多等待该次请求结束／超时，下一条不会继续抢占。

```sh
.venv/bin/python scripts/evaluate_quality.py asr --engine whisper --run-local-models --output /private/tmp/whisper-candidate.json
.venv/bin/python scripts/evaluate_quality.py asr --engine qwen --run-local-models --output /private/tmp/qwen-candidate.json
.venv/bin/python scripts/evaluate_quality.py compare --baseline evaluation/baselines/whisper-tts-v1.json --candidate /private/tmp/whisper-candidate.json --output /private/tmp/whisper-comparison.json
```

显式本机推理只使用已安装的文件，不读取真实会议或云端 LLM 配置，不打开麦克风。耗时包含每条独立进程的启动、模型加载及生产转写／对齐路径，不保证磁盘缓存为冷；Qwen 与 Whisper 路径不同，不能只按该时间比较解码速度。报告记录 provider 路径／大小／mtime 身份摘要（不是完整权重校验和）、实现摘要、系统、请求模式和时长。

如果已有固定样本输出，可离线评分，输入形如 `{"outputs":{"asr-proposal":"...","asr-change":"...","asr-domain":"...","asr-numbers":"..."}}`：

```sh
.venv/bin/python scripts/evaluate_quality.py score-asr --input /private/tmp/predictions.json --output /private/tmp/imported-report.json
```

导入结果明确标为未独立执行，不继承“实际模型运行”身份；默认报告不保留导入原文，只保留输出摘要和计数。所有 ASR ID 必须完整且无额外 ID，缺失请求不能静默从分母剔除。

## 固定口径与回归

- 音频为本机已安装 Tingting 语音、每分钟 185 字生成，并转为 PCM16、16kHz 单声道；最终 WAV 和逐文件 SHA256 固定入库，评分不重新合成。`generate_audio.py` 仅在显式 `--synthesize` 和新目录时生成候选音频，不改写现有真值／固定集；换音色或重新生成是新的输入版本，不能悄悄代替旧音频。
- 文本按 NFKC、大小写折叠、删除 Unicode 标点和空白归一化，保留数字书写差异。不把“五万”和“5万”自动等价。CER 为编辑距离／参考字符数；总体按参考字符数加权，不简单平均句子比例，插入过多时 CER 可以超过 100%。
- 报告绑定完整评测集／概览输入指纹、评分代码指纹、版本、样本 ID 和报告完整性指纹，逐条计数与汇总重新核对。缺样本、重复样本、输入／评分口径或模式不同拒绝直接比较。指纹用于复现和漂移检查，不是来源可信性的数字签名。
- 协议探针逐项比较，原本通过的任一探针退化即失败；ASR 首版按总体 CER 增量比较，默认容差 0.005（0.5 个百分点），不是统计显著性或单句改善保证，仍需查看逐段结果。耗时保留观察，不作为自动失败门槛。当前 CLI 返回 0 表示成功，回归／离线探针失败返回 1，无法完成／输入错误返回 2。
- 新报告默认只写新文件；人工审阅范围和逐条差异后，再通过 Git 提交新的正式基线。本轮版本化报告仅含自编合成数据，临时跑分放 `evaluation/runs/`（已忽略）；勿把真实会议或云端认证配置复制到公开固定样本／基线。

运行管理 → 运行环境 → 固定样本质量基线仅被动查看已保存报告，过期口径会标为待重建，Qwen 等未测引擎显示原因，不自动推理。

## 后续 F2

优先补齐 Qwen 驻留后的同输入实测；再加入经授权且独立人工标注的普通话会议片段、专名／金额／日期、噪声／重叠发言、长会议边界和真实检索问答。LLM 纪要覆盖、提议误升决定、引用语义支持与拒答质量需要人工标注及复核，不用关键词命中冒充语义评分。中文数字等价、标点、对齐边界、DER、实时首字／确认延迟和内存功耗应作为独立指标版本开发，不回填为本轮已完成。
