"""Temporarily uncover an exact Xorg client without requesting keyboard focus."""
import ctypes as C
from group_text import title_name


class Changes(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('width', C.c_int),
                ('height', C.c_int), ('border_width', C.c_int),
                ('sibling', C.c_ulong), ('stack_mode', C.c_int)]


def restore_neighbor(original, current, target):
    if target not in current or target not in original:
        return None
    index = original.index(target)
    for sibling in original[index+1:]:
        if sibling in current:
            return sibling, 1  # Below the nearest surviving original sibling above.
    for sibling in reversed(original[:index]):
        if sibling in current:
            return sibling, 0  # Above the nearest surviving original sibling below.
    return None


class WindowStack:
    def __init__(self, connection):
        self.c = connection
        x, bind = connection.x, connection.bind
        bind(x, 'XInternAtom', C.c_ulong, [C.c_void_p, C.c_char_p, C.c_int])
        bind(x, 'XGetWindowProperty', C.c_int, [C.c_void_p, C.c_ulong, C.c_ulong,
             C.c_long, C.c_long, C.c_int, C.c_ulong, C.POINTER(C.c_ulong),
             C.POINTER(C.c_int), C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)])
        bind(x, 'XQueryTree', C.c_int, [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong),
             C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)])
        bind(x, 'XRaiseWindow', C.c_int, [C.c_void_p, C.c_ulong])
        bind(x, 'XConfigureWindow', C.c_int, [C.c_void_p, C.c_ulong, C.c_uint, C.POINTER(Changes)])
        bind(x, 'XFree', C.c_int, [C.c_void_p])

    def property(self, window, name, expected):
        x, display = self.c.x, self.c.display
        atom = x.XInternAtom(display, name.encode(), 1)
        expected_atom = x.XInternAtom(display, expected.encode(), 1)
        if not atom or not expected_atom:
            return None
        actual, count, remaining = C.c_ulong(), C.c_ulong(), C.c_ulong()
        fmt, data = C.c_int(), C.c_void_p()
        result = x.XGetWindowProperty(display, window, atom, 0, 4096, 0, expected_atom,
            C.byref(actual), C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data))
        try:
            if result or actual.value != expected_atom or remaining.value or not data.value:
                return None
            if fmt.value == 32:
                return list(C.cast(data, C.POINTER(C.c_ulong))[:count.value])
            if fmt.value == 8:
                return C.string_at(data, count.value).decode('utf-8', errors='replace')
            return None
        finally:
            if data.value:
                x.XFree(data)

    def tree(self, window):
        root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
        children = C.POINTER(C.c_ulong)()
        if not self.c.x.XQueryTree(self.c.display, window, C.byref(root), C.byref(parent),
                                  C.byref(children), C.byref(count)):
            raise RuntimeError('无法读取目标窗口层级。')
        try:
            return parent.value, list(children[:count.value]) if children else []
        finally:
            if children:
                self.c.x.XFree(C.cast(children, C.c_void_p))

    def target(self, name, pid):
        clients = self.property(self.c.root, '_NET_CLIENT_LIST', 'WINDOW') or []
        found = []
        for window in clients:
            if self.property(window, '_NET_WM_PID', 'CARDINAL') != [pid]:
                continue
            title = self.property(window, '_NET_WM_NAME', 'UTF8_STRING')
            if title is None:
                title = self.property(window, 'WM_NAME', 'STRING')
            if title is not None and title_name(title) == title_name(name):
                found.append(window)
        if len(found) != 1:
            raise RuntimeError('独立窗口的 Xorg 标题／进程匹配不唯一。')
        target = found[0]
        for _ in range(32):
            parent, _ = self.tree(target)
            if parent == self.c.root:
                return target
            if not parent:
                break
            target = parent
        raise RuntimeError('独立窗口不属于当前桌面。')

    def expose(self, name, pid):
        return Exposure(self, name, pid)


class Exposure:
    def __init__(self, stack, name, pid):
        self.stack, self.name, self.pid = stack, name, pid
        self.target = None
        self.original = []

    def restore(self):
        if self.target is None:
            return
        stack, c = self.stack, self.stack.c
        _, current = stack.tree(c.root)
        neighbor = restore_neighbor(self.original, current, self.target)
        if neighbor:
            sibling, mode = neighbor
            values = Changes(sibling=sibling, stack_mode=mode)
            c.x.XConfigureWindow(c.display, self.target, (1 << 5) | (1 << 6), C.byref(values))
            c.x.XSync(c.display, 0)

    def __enter__(self):
        c = self.stack.c
        self.target = self.stack.target(self.name, self.pid)
        _, self.original = self.stack.tree(c.root)
        before = c.status()['primary']
        self.primary = before
        c.x.XRaiseWindow(c.display, self.target)
        c.x.XSync(c.display, 0)
        if c.status()['primary'] != before:
            self.restore()
            raise RuntimeError('调整窗口层级时主鼠标或焦点变化，未发送。')
        return self

    def __exit__(self, *args):
        self.restore()
        if self.stack.c.status()['primary'] != self.primary:
            raise RuntimeError('恢复窗口层级后主鼠标或焦点变化，停止。')
