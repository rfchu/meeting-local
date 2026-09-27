#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-qwen
.venv-qwen/bin/pip install -r requirements-qwen-lock.txt
.venv-qwen/bin/python scripts/download_qwen.py
.venv-qwen/bin/python - <<'PY'
import json
from pathlib import Path
from importlib.metadata import version
packages=['mlx-audio','mlx','mlx-metal','transformers','tokenizers','huggingface-hub','numpy']
Path('.venv-qwen/qwen-runtime.json').write_text(json.dumps({p:version(p) for p in packages},indent=2))
print('千问 ASR 和时间戳对齐模型已准备完成。刷新应用后可选择千问引擎。')
PY
