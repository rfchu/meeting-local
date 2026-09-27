#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../frontend"
npm ci
npm run build
echo 'React 前端已构建到 app/static/'
