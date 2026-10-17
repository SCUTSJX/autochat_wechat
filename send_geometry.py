"""Verify send-button geometry and the window owning it."""
import ctypes as C
from atspi_backend import bounds

def unobstructed_pid(connection, x, y):
    """Walk the topmost X windows under the point and read _NET_WM_PID."""
    lib = connection.x
    bind = connection.bind
    bind(lib, 'XTranslateCoordinates', C.c_int,
         [C.c_void_p, C.c_ulong, C.c_ulong, C.c_int, C.c_int,
          C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_ulong)])
    bind(lib, 'XInternAtom', C.c_ulong, [C.c_void_p, C.c_char_p, C.c_int])
    bind(lib, 'XGetWindowProperty', C.c_int,
         [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long, C.c_int,
          C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
          C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)])
    bind(lib, 'XFree', C.c_int, [C.c_void_p])
    atom = lib.XInternAtom(connection.display, b'_NET_WM_PID', 1)
    cardinal = lib.XInternAtom(connection.display, b'CARDINAL', 1)
    if not atom or not cardinal:
        raise RuntimeError('无法核验点击位置上的窗口进程，未点击。')
    current, owner = connection.root, None
    for _ in range(64):
        actual, count, remaining = C.c_ulong(), C.c_ulong(), C.c_ulong()
        fmt, data = C.c_int(), C.c_void_p()
        result = lib.XGetWindowProperty(connection.display, current, atom, 0, 1, 0,
            cardinal, C.byref(actual), C.byref(fmt), C.byref(count),
            C.byref(remaining), C.byref(data))
        try:
            if result == 0 and actual.value == cardinal and fmt.value == 32 and count.value == 1 and data.value:
                owner = C.cast(data, C.POINTER(C.c_ulong))[0]
        finally:
            if data.value:
                lib.XFree(data)
        dx, dy, child = C.c_int(), C.c_int(), C.c_ulong()
        if not lib.XTranslateCoordinates(connection.display, connection.root,
            current, x, y, C.byref(dx), C.byref(dy), C.byref(child)):
            raise RuntimeError('窗口位置检查失败，未点击。')
        if not child.value:
            return owner
        current = child.value
    raise RuntimeError('点击位置的窗口层级异常，未点击。')

def button_point(connection, root, button):
    x, y, width, height = bounds(button)
    if width <= 0 or height <= 0:
        raise RuntimeError('Send 按钮不可见，未点击。')
    point = (x + width//2, y + height//2)
    if unobstructed_pid(connection, *point) != root.get_process_id():
        raise RuntimeError('Send 按钮被其他窗口遮挡或目标进程不匹配；请保持目标聊天窗口可见。')
    return point
