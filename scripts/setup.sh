#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo '请先安装 FFmpeg，例如 brew install ffmpeg'; exit 1; }
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
mkdir -p vendor models
if [[ ! -d vendor/whisper.cpp/.git ]]; then
  git clone https://github.com/ggml-org/whisper.cpp.git vendor/whisper.cpp
  git -C vendor/whisper.cpp checkout d09f61a708f3487afa956ff578e60eae5e7a233c
fi
sdk_args=()
if [[ -d /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk ]]; then
  sdk_args=(-DCMAKE_OSX_SYSROOT=/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk)
fi
.venv/bin/cmake -S vendor/whisper.cpp -B vendor/whisper.cpp/build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF "${sdk_args[@]}"
.venv/bin/cmake --build vendor/whisper.cpp/build --config Release -j 8
fetch(){
  local target="$1" source="$2"
  if [[ ! -s "$target" ]]; then
    curl -L --fail --retry 3 -o "$target.part" "$source"
    mv "$target.part" "$target"
  fi
}
fetch models/ggml-large-v3-turbo.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
fetch models/ggml-silero-v6.2.0.bin https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin
fetch models/segmentation.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
if [[ ! -f models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx ]]; then
  tar -xjf models/segmentation.tar.bz2 -C models
fi
fetch models/3dspeaker.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx
fetch models/punctuation.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2
if [[ ! -f models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8/model.int8.onnx ]]; then
  tar -xjf models/punctuation.tar.bz2 -C models
fi
.venv/bin/python scripts/verify_models.py
echo '安装完成。运行 ./start.command，打开 http://127.0.0.1:8765'
