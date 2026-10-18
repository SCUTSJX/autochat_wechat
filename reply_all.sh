#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo "安装：bash reply_all.sh install"
  echo "账号初始化：bash reply_all.sh init [--contact 好友窗口名称]"
  echo "检查环境与配置：bash reply_all.sh check"
  echo "查看表情状态：bash reply_all.sh stickers status"
  echo "检查草稿后恢复会话：bash reply_all.sh stickers resume --conversation 会话ID"
  echo "自动回复：bash reply_all.sh [--preview]"
  exit 0
fi
if [[ "$(uname -s)" != Linux ]]; then
  echo "请在 Ubuntu 图形桌面运行。" >&2
  exit 1
fi
if [[ "${1:-}" == install || "${1:-}" == --install ]]; then
  if [[ "$#" -ne 1 ]]; then
    echo "安装用法：bash reply_all.sh install" >&2
    exit 1
  fi
  if [[ "$EUID" -eq 0 ]]; then
    echo "请用普通用户运行 bash reply_all.sh install，不要在命令前加 sudo。" >&2
    exit 1
  fi
  if [[ -L .venv ]]; then
    echo ".venv 不能是指向其他目录的链接，请使用新解压的项目目录。" >&2
    exit 1
  fi
  if ! command -v apt-get >/dev/null 2>&1 || ! command -v sudo >/dev/null 2>&1; then
    echo "此安装命令需要 Ubuntu 的 apt-get 和 sudo。" >&2
    exit 1
  fi
  export PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
  echo "安装 Ubuntu 辅助功能、截图及虚拟光标依赖……"
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip python3-gi \
    gir1.2-atspi-2.0 gir1.2-gtk-3.0 at-spi2-core dbus-x11 \
    xinput libx11-6 libxi6 libxtst6
  echo "在当前项目目录创建 Python 环境……"
  python3 -m venv --system-site-packages .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python - <<'PY'
import ctypes
import ctypes.util
import httpx
import frida
from PIL import Image
import gi

gi.require_version('Atspi', '2.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Atspi, Gdk

for name in ('X11', 'Xi', 'Xtst'):
    library = ctypes.util.find_library(name)
    if not library:
        raise RuntimeError('缺少系统库：' + name)
    ctypes.CDLL(library)
print('Python、辅助功能、截图及 X11 库检查通过。')
PY
  .venv/bin/python -B project_setup.py
  echo "安装完成。运行 bash wechat.sh 登录微信，bash switch_api.sh 配置接口，然后运行 bash reply_all.sh init。"
  exit 0
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "缺少 .venv/bin/python，请先用普通用户运行 bash reply_all.sh install。" >&2
  exit 1
fi
if [[ "${1:-}" == stickers ]]; then
  shift
  export PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
  exec .venv/bin/python -B sticker_state.py "$@"
fi
if [[ "${1:-}" == check || "${1:-}" == --check ]]; then
  if [[ "$#" -ne 1 || "$EUID" -eq 0 ]]; then
    echo "检查用法：普通桌面用户运行 bash reply_all.sh check，不要加 sudo。" >&2
    exit 1
  fi
  export PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
  .venv/bin/python -B project_setup.py
  exec .venv/bin/python -B environment_check.py
fi
if [[ "$EUID" -ne 0 ]]; then
  exec sudo --preserve-env=DISPLAY,XAUTHORITY,DBUS_SESSION_BUS_ADDRESS,AT_SPI_BUS_ADDRESS,XDG_SESSION_TYPE \
    /bin/bash "$PWD/reply_all.sh" "$@"
fi
if [[ -z "${SUDO_UID:-}" || "$SUDO_UID" == 0 ]]; then
  echo "必须从已登录微信的普通用户桌面启动。" >&2
  exit 1
fi
export PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
if [[ "${1:-}" == init || "${1:-}" == --init ]]; then
  shift
  exec .venv/bin/python -B account_init.py "$@"
fi
exec .venv/bin/python -u run_all.py "$@"
