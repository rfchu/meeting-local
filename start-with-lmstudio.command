#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ -z "${LM_API_TOKEN:-}" ]]; then
  printf '请输入 LM Studio API Token（不会保存或显示）：'
  read -rs LM_API_TOKEN
  printf '\n'
  export LM_API_TOKEN
fi
exec ./start.command
