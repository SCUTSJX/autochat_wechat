"""Ubuntu 20.04/Xorg second pointer, controlled via CLI or local JSON socket."""
import argparse
import ctypes as C
import ctypes.util
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

NAME = 'WechatBotCursor'


def run(*args):
    return subprocess.check_output(['xinput'] + list(args), text=True,
                                   stderr=subprocess.STDOUT, timeout=5).strip()


def device(name):
    try:
        value = run('list', '--id-only', name)
    except subprocess.CalledProcessError:
        return None
    if not value.isdecimal():
        raise RuntimeError('设备名称不唯一：' + name)
    return int(value)


def environment():
    if not sys.platform.startswith('linux') or os.geteuid() == 0:
        raise RuntimeError('请在 Ubuntu 桌面以普通用户运行，不要使用 sudo。')
    if os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        raise RuntimeError('需要 Xorg；请注销后选择 Ubuntu on Xorg。')
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('缺少 DISPLAY，请在已登录的图形桌面终端运行。')


class XConnection:
    def __init__(self):
        environment()
        self.x = C.CDLL(ctypes_library('X11'))
        self.xi = C.CDLL(ctypes_library('Xi'))
        self.xt = C.CDLL(ctypes_library('Xtst'))
        self.bind(self.x, 'XOpenDisplay', C.c_void_p, [C.c_char_p])
        self.bind(self.x, 'XDefaultRootWindow', C.c_ulong, [C.c_void_p])
        self.bind(self.x, 'XDefaultScreen', C.c_int, [C.c_void_p])
        self.bind(self.x, 'XDisplayWidth', C.c_int, [C.c_void_p, C.c_int])
        self.bind(self.x, 'XDisplayHeight', C.c_int, [C.c_void_p, C.c_int])
        self.bind(self.x, 'XSync', C.c_int, [C.c_void_p, C.c_int])
        self.bind(self.x, 'XCloseDisplay', C.c_int, [C.c_void_p])
        self.bind(self.x, 'XQueryPointer', C.c_int, [C.c_void_p, C.c_ulong,
                  C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                  C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_int),
                  C.POINTER(C.c_uint)])
        self.bind(self.x, 'XGetInputFocus', C.c_int,
                  [C.c_void_p, C.POINTER(C.c_ulong), C.POINTER(C.c_int)])
        self.bind(self.xi, 'XIQueryVersion', C.c_int,
                  [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)])
        self.bind(self.xi, 'XISetClientPointer', C.c_int,
                  [C.c_void_p, C.c_ulong, C.c_int])
        self.bind(self.xi, 'XIGetClientPointer', C.c_int,
                  [C.c_void_p, C.c_ulong, C.POINTER(C.c_int)])
        self.bind(self.xt, 'XTestQueryExtension', C.c_int,
                  [C.c_void_p] + [C.POINTER(C.c_int)] * 4)
        self.bind(self.xt, 'XTestFakeMotionEvent', C.c_int,
                  [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong])
        self.bind(self.xt, 'XTestFakeButtonEvent', C.c_int,
                  [C.c_void_p, C.c_uint, C.c_int, C.c_ulong])
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('无法连接当前 X 显示会话。')
        major, minor = C.c_int(2), C.c_int(0)
        if self.xi.XIQueryVersion(self.display, C.byref(major), C.byref(minor)) != 0:
            raise RuntimeError('X Input 2.0 不可用。')
        values = [C.c_int() for _ in range(4)]
        if not self.xt.XTestQueryExtension(self.display, *[C.byref(v) for v in values]):
            raise RuntimeError('XTEST 不可用。')
        self.root = self.x.XDefaultRootWindow(self.display)
        self.screen = self.x.XDefaultScreen(self.display)
        self.primary = device('Virtual core pointer')
        self.pointer = device(NAME + ' pointer')
        if self.pointer is None or self.primary is None or self.pointer == self.primary:
            raise RuntimeError('先执行 create 创建独立指针。')
        self.select(self.pointer)

    @staticmethod
    def bind(lib, name, result, args):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, args

    def select(self, identifier):
        # Window None changes ONLY this connection's ClientPointer, not WeChat,
        # the window manager, physical devices or any other client's pointer.
        self.xi.XISetClientPointer(self.display, 0, identifier)
        self.x.XSync(self.display, 0)
        actual = C.c_int()
        if not self.xi.XIGetClientPointer(self.display, 0, C.byref(actual)) or actual.value != identifier:
            raise RuntimeError('独立指针绑定失败，禁止注入默认鼠标事件。')

    def snapshot(self, identifier):
        self.select(identifier)
        root, child, focus = C.c_ulong(), C.c_ulong(), C.c_ulong()
        rx, ry, wx, wy, revert = [C.c_int() for _ in range(5)]
        mask = C.c_uint()
        if not self.x.XQueryPointer(self.display, self.root, C.byref(root), C.byref(child),
                                   C.byref(rx), C.byref(ry), C.byref(wx), C.byref(wy), C.byref(mask)):
            raise RuntimeError('无法读取指针位置。')
        self.x.XGetInputFocus(self.display, C.byref(focus), C.byref(revert))
        self.select(self.pointer)
        return {'x': rx.value, 'y': ry.value, 'focus_window': focus.value}

    def status(self):
        return {'display': os.environ['DISPLAY'], 'pointer_id': self.pointer,
                'primary_id': self.primary, 'virtual': self.snapshot(self.pointer),
                'primary': self.snapshot(self.primary)}

    def move(self, x, y):
        if type(x) is not int or type(y) is not int:
            raise ValueError('坐标必须为整数。')
        width = self.x.XDisplayWidth(self.display, self.screen)
        height = self.x.XDisplayHeight(self.display, self.screen)
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError('坐标超出当前屏幕范围。')
        self.select(self.pointer)
        if not self.xt.XTestFakeMotionEvent(self.display, self.screen, x, y, 0):
            raise RuntimeError('虚拟指针移动请求失败。')
        self.x.XSync(self.display, 0)
        result = self.snapshot(self.pointer)
        if (result['x'], result['y']) != (x, y):
            raise RuntimeError('虚拟指针位置回读失败；不执行点击。')

    def click(self, button):
        if type(button) is not int or button not in (1, 2, 3):
            raise ValueError('按钮仅支持 1 左键、2 中键、3 右键。')
        self.select(self.pointer)
        released = False
        try:
            if not self.xt.XTestFakeButtonEvent(self.display, button, 1, 0):
                raise RuntimeError('按下请求失败。')
        finally:
            released = bool(self.xt.XTestFakeButtonEvent(self.display, button, 0, 0))
            self.x.XSync(self.display, 0)
        if not released:
            raise RuntimeError('释放请求失败；停止控制，不自动重试点击。')

    def drag(self, start, end, duration=1.2, steps=48, validate=None):
        """One held secondary-button gesture; always release, including cancellation."""
        if (not isinstance(start, (list, tuple)) or len(start) != 2
                or not isinstance(end, (list, tuple)) or len(end) != 2
                or any(type(v) is not int for v in list(start) + list(end))
                or type(steps) is not int or not 8 <= steps <= 240
                or not isinstance(duration, (int, float)) or not 0.3 <= duration <= 10):
            raise ValueError('拖拽坐标、步数或时长无效。')
        width = self.x.XDisplayWidth(self.display, self.screen)
        height = self.x.XDisplayHeight(self.display, self.screen)
        if any(not (0 <= p[0] < width and 0 <= p[1] < height) for p in (start, end)):
            raise ValueError('拖拽坐标超出当前屏幕。')
        self.move(*start)
        if validate:
            validate()
        before = self.status()['primary']
        try:
            self.select(self.pointer)
            if not self.xt.XTestFakeButtonEvent(self.display, 1, 1, 0):
                raise RuntimeError('拖拽按下失败。')
            self.x.XSync(self.display, 0)
            time.sleep(0.15)
            for index in range(1, steps + 1):
                self.move(*(round(a + (b-a)*index/steps) for a, b in zip(start, end)))
                time.sleep(duration/steps)
                if self.status()['primary'] != before:
                    raise RuntimeError('拖拽期间主鼠标或焦点变化，已中止。')
            if validate:
                validate()
            time.sleep(0.2)
        finally:
            self.select(self.pointer)
            released = self.xt.XTestFakeButtonEvent(self.display, 1, 0, 0)
            self.x.XSync(self.display, 0)
            if not released:
                raise RuntimeError('拖拽释放失败，请停止指针控制。')

    def execute(self, request):
        operation = request.get('op')
        if operation == 'status':
            return self.status()
        if operation not in ('move', 'click', 'drag', 'self-test'):
            raise ValueError('仅接受 status / move / click / drag / self-test。')
        before = self.status()
        if operation == 'self-test':
            old = before['virtual']
            width = self.x.XDisplayWidth(self.display, self.screen)
            self.move((old['x'] + 20) % width, old['y'])
            moved = self.status()
            self.move(old['x'], old['y'])
            return {'before': before, 'moved': moved, 'after': self.status(),
                    'primary_unchanged': before['primary'] == moved['primary'],
                    'clicked': False}
        if operation == 'drag':
            self.drag(request.get('start'), request.get('end'), request.get('duration', 1.2))
        if operation == 'move' or 'x' in request or 'y' in request:
            self.move(request.get('x'), request.get('y'))
        if operation == 'click':
            self.click(request.get('button', 1))
        after = self.status()
        return {'before': before, 'after': after,
                'primary_unchanged': before['primary'] == after['primary'],
                'click_requested': operation == 'click',
                'application_activation_verified': False}

    def close(self):
        self.x.XCloseDisplay(self.display)


def ctypes_library(name):
    value = ctypes.util.find_library(name)
    if not value:
        raise RuntimeError('缺少动态库：' + name)
    return value


def serve(connection, path):
    # Local filesystem access only. No TCP listener and no shell execution.
    parent = Path(path).parent
    if parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
        raise RuntimeError('控制 socket 必须位于当前用户拥有且权限 700 的目录。')
    with open(path + '.lock', 'a') as lock:
        os.chmod(path + '.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(path):
            raise RuntimeError('socket 已存在；确认旧服务已退出后再删除该文件。')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        os.chmod(path, 0o600)
        server.listen(4)
        print(json.dumps({'ready': True, 'socket': path}), flush=True)
        try:
            while True:
                client, _ = server.accept()
                with client:
                    client.settimeout(3)
                    try:
                        data = b''
                        while b'\n' not in data and len(data) <= 4096:
                            part = client.recv(4096)
                            if not part:
                                break
                            data += part
                        if len(data) > 4096 or b'\n' not in data:
                            raise ValueError('需要一行不超过 4096 字节的 JSON。')
                        request = json.loads(data.split(b'\n', 1)[0])
                        if not isinstance(request, dict):
                            raise ValueError('请求必须为 JSON 对象。')
                        response = {'ok': True, 'result': connection.execute(request)}
                    except Exception as exc:
                        response = {'ok': False, 'error': str(exc)}
                    try:
                        client.sendall((json.dumps(response, ensure_ascii=False)+'\n').encode())
                    except OSError:
                        pass
        finally:
            server.close()
            os.unlink(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['create', 'remove', 'status', 'move', 'click', 'self-test', 'serve'])
    parser.add_argument('x', nargs='?', type=int)
    parser.add_argument('y', nargs='?', type=int)
    parser.add_argument('--button', type=int, default=1)
    parser.add_argument('--socket', default=os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'wechat-virtual-cursor.sock'))
    args = parser.parse_args()
    environment()
    if args.command == 'create':
        if device(NAME + ' pointer') is None:
            run('create-master', NAME, '1', '1')
    if args.command == 'remove':
        identifier = device(NAME + ' pointer')
        if identifier is not None:
            if identifier == device('Virtual core pointer'):
                raise RuntimeError('拒绝删除主鼠标。')
            run('remove-master', str(identifier), 'Floating')
        print(json.dumps({'removed': True}))
        return
    connection = XConnection()
    try:
        if args.command == 'serve':
            serve(connection, args.socket)
        else:
            request = {'op': 'status' if args.command == 'create' else args.command,
                       'button': args.button}
            if args.x is not None or args.y is not None:
                request.update(x=args.x, y=args.y)
            print(json.dumps(connection.execute(request), ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('错误：' + str(exc), file=sys.stderr)
        sys.exit(1)
