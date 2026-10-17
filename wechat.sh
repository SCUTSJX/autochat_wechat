#!/usr/bin/env bash
set -euo pipefail
export QT_ACCESSIBILITY=1
export QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
if [[ $# -gt 0 ]]; then
  exec "$@"
elif command -v wechat >/dev/null 2>&1; then
  exec wechat
elif [[ -x /opt/wechat/wechat ]]; then
  exec /opt/wechat/wechat
else
  echo "Pass the installed Linux WeChat executable path as argument." >&2
  exit 1
fi
