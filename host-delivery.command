#!/bin/zsh
set -e
cd "$(dirname "$0")"
# Read-only by default; explicit build / rollback / recover never stop the service.
exec python3 scripts/host_delivery.py "${@:-status}"
