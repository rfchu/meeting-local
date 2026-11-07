# 开发与验证

后端使用 FastAPI 提供本机接口，SQLite 保存会议、任务和版本记录。录音与音频处理、转写、纪要生成分别通过对应的服务和任务入口工作。React 前端的生产构建由后端从 `app/static/` 提供。Swift 宿主与原生采集器是可选的 macOS 组件，采集边界参见[原生采集协议](CAPTURE_PROTOCOL.md)。

在仓库根目录执行：

```bash
.venv/bin/python -m pytest
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run test:stream
npm --prefix frontend run build
bash scripts/test_host.sh
```

界面和端到端验证使用隔离数据目录。相关演示入口位于 `scripts/`，历史验证步骤见[工程演进与历史验收](DELIVERY_HISTORY.md)。涉及真实采集设备的操作见[硬件验收](HARDWARE_ACCEPTANCE.md)。

主要上游项目包括 [whisper.cpp](https://github.com/ggml-org/whisper.cpp)、[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[Silero VAD](https://github.com/snakers4/silero-vad)、[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)、[sqlite-vec](https://github.com/asg017/sqlite-vec) 和 [BGE](https://huggingface.co/BAAI/bge-small-zh-v1.5)。分发第三方依赖和模型时，请查阅对应版本的上游许可。
