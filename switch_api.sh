#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != Linux ]]; then
  echo "请在 Ubuntu 桌面运行。" >&2
  exit 1
fi
if [[ "$EUID" -eq 0 ]]; then
  echo "请用普通桌面用户运行 bash switch_api.sh，无需 sudo。" >&2
  exit 1
fi
export PYTHONUTF8=1
exec /usr/bin/python3 -B "$PWD/api_switcher.py" "$@"
