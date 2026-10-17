"""Read-only environment/profile checks: no Frida attachment, API calls or sends."""
import argparse
import ctypes
import ctypes.util
import hashlib
import importlib
import os
from pathlib import Path
import shutil
import sys

from project_setup import ROOT, load_profile, read_object


def check_chat_windows():
    from atspi_backend import application
    from multi_window_sender import discover
    diagnostics = {'visible_frames': 0}
    found = discover(application(), diagnostics)
    if not found:
        raise RuntimeError('请打开至少一个有消息列表及发送控件的聊天独立窗口。')
    return len(found)


def inspect(root=ROOT):
    root = Path(root)
    errors = []
    def check(label, action):
        try:
            action()
            print('[通过] ' + label)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            print('[未通过] ' + label + '：' + detail)
            errors.append(label)
    def desktop():
        if not sys.platform.startswith('linux') or not os.environ.get('DISPLAY'):
            raise RuntimeError('请在 Ubuntu 已登录的图形终端运行。')
        if os.environ.get('XDG_SESSION_TYPE') == 'wayland':
            raise RuntimeError('请注销并选择 Ubuntu on Xorg。')
    check('Xorg 图形会话', desktop)
    def dependencies():
        for name in ('httpx', 'frida', 'PIL.Image', 'gi'):
            importlib.import_module(name)
        import gi
        gi.require_version('Atspi', '2.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Atspi, Gdk
        for name in ('X11', 'Xi', 'Xtst'):
            library = ctypes.util.find_library(name)
            if not library:
                raise RuntimeError('缺少 ' + name + '；运行 bash reply_all.sh install。')
            ctypes.CDLL(library)
        if not shutil.which('xinput'):
            raise RuntimeError('缺少 xinput；运行 bash reply_all.sh install。')
    check('Python、辅助功能及 X11 依赖', dependencies)
    def client():
        from native_receiver import current_wechat_pid, validate_candidate
        layout = read_object(root / 'wechat-layout.json')
        validate_candidate(layout)
        executable = Path('/proc') / str(current_wechat_pid()) / 'exe'
        digest = hashlib.sha256()
        with executable.open('rb') as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(part)
        if digest.hexdigest() != layout['sha256']:
            raise RuntimeError('微信程序不匹配支持的版本构建；重新 init 不能修复版本不兼容。')
    check('唯一微信进程及受支持的程序指纹', client)
    def account():
        from native_receiver import validate_layout
        validate_layout(load_profile(root))
    check('本地账号校准文件', account)
    def config():
        from core import settings
        if root != ROOT:
            raise RuntimeError('检查器只检查自己的项目目录。')
        settings()
    check('回复策略与群昵称配置', config)
    def apis():
        from api_profiles import active_profile
        for channel in ('text', 'vision'):
            profile = active_profile(root, channel)
            if not profile or profile['base_url'].startswith('https://example.com'):
                raise RuntimeError('请运行 bash switch_api.sh 填写文字和图片接口。')
    check('文字及图片 API 配置（不发请求）', apis)
    check('聊天独立窗口可发现', check_chat_windows)
    print('账号文件通过检查不等于已核实当前登录账号；切换账号后先重新 init。')
    print('检查不会发送消息或验证 API 连通性；连通性测试请在 API 面板主动执行。')
    return 1 if errors else 0


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.exit(inspect())
