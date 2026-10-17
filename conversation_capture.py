"""Screenshot an already unobstructed conversation; never change window stacking."""
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import sys
import time

BEIJING = timezone(timedelta(hours=8))


def frame_candidates(root):
    """Discover window handles once; never descend into a frame's message tree."""
    pending = [root]
    while pending:
        node = pending.pop()
        role = node.get_role_name()
        if role == 'frame':
            yield node
            continue
        if role in ('list', 'table', 'tree'):
            continue
        for index in reversed(range(node.get_child_count())):
            child = node.get_child_at_index(index)
            if child is not None:
                pending.append(child)


class ConversationWindow:
    def __init__(self, chat):
        from atspi_backend import application
        from ui_helpers import unique
        self.chat = chat
        root = application()
        self.pid = root.get_process_id()
        self.frame = unique([node for node in frame_candidates(root)
                             if node.get_name() == chat], '指定会话窗口')

    def geometry(self):
        from atspi_backend import bounds
        if (self.frame.get_role_name() != 'frame' or self.frame.get_name() != self.chat
                or self.frame.get_process_id() != self.pid):
            raise RuntimeError('已缓存的会话窗口失效，请重新启动测试。')
        return self.pid, bounds(self.frame)


def save_capture(directory, data):
    now = datetime.now(BEIJING)
    stem = now.strftime('%Y%m%d_%H%M%S_%f')
    for index in range(10000):
        name = stem + ('_%d' % index if index else '') + '.png'
        path = directory/name
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        return name, now.isoformat()
    raise RuntimeError('截图时间戳冲突过多。')


def pixbuf_png(pixbuf):
    from PIL import Image
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if min(width, height) <= 0 or width*height > 40000000:
        raise RuntimeError('实际截图像素尺寸无效。')
    mode = 'RGBA' if pixbuf.get_has_alpha() else 'RGB'
    image = Image.frombytes(mode, (width, height), bytes(pixbuf.get_pixels()),
                            'raw', mode, pixbuf.get_rowstride())
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def capture(window, directory, screen, grab, progress=None):
    """The only operation after save_capture is constructing the reply."""
    stage = progress or (lambda _: None)
    stage('读取会话位置')
    pid, rect = window.geometry()
    x, y, width, height = rect
    if screen is None:
        raise RuntimeError('无法读取 Xorg 桌面。')
    if (min(width, height) <= 0 or width*height > 40000000
            or x < 0 or y < 0 or x+width > screen.get_width() or y+height > screen.get_height()):
        raise RuntimeError('会话窗口不完整可见或几何无效，请保持窗口在屏幕内。')
    time.sleep(0.5)  # Allow the newly received thumbnail to paint before capture.
    if window.geometry() != (pid, rect):
        raise RuntimeError('截图前会话窗口发生变化。')
    stage('截取窗口')
    pixbuf = grab(screen, x, y, width, height)
    if pixbuf is None:
        raise RuntimeError('会话截图为空。')
    stage('编码 PNG')
    data = pixbuf_png(pixbuf)
    stage('保存 PNG；随后立即返回')
    name, timestamp = save_capture(directory, data)
    return dict(ok=True, screenshot=name, captured_at=timestamp,
                ui_changed=None, ui_change_checked=False, bounds=list(rect))


def serve(chat, directory):
    import gi
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk
    if os.geteuid() == 0:
        raise RuntimeError('截图进程必须以桌面普通用户运行。')
    Gdk.init([])
    if Gdk.Display.get_default() is None:
        raise RuntimeError('未连接到桌面显示。')
    window = ConversationWindow(chat)
    window.geometry()
    screen = Gdk.get_default_root_window()
    print(json.dumps({'ready': True}), flush=True)
    def progress(message):
        # Keep progress off the stdout JSON protocol; no post-save cleanup.
        print('截图阶段：'+message, file=sys.stderr, flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get('op') != 'capture':
                raise RuntimeError('不支持的截图操作。')
            result = capture(window, directory, screen, Gdk.pixbuf_get_from_window, progress)
        except Exception as exc:
            result = dict(ok=False, error_type=type(exc).__name__)
            if type(exc) is RuntimeError:
                result['error'] = str(exc)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chat', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    try:
        serve(args.chat, args.directory.resolve(strict=True))
    except Exception as exc:
        print(json.dumps(dict(ready=False, error_type=type(exc).__name__), ensure_ascii=False), flush=True)
        sys.exit(1)
