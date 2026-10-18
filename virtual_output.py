"""Reusable AT-SPI input + secondary-pointer output, no primary fallback."""
import time
from ui_helpers import rows


def matches(row, value):
    # Match an entire contiguous block of lines, not a substring in old text.
    lines = row.strip().splitlines()
    wanted = value.strip().splitlines()
    return bool(wanted) and any(lines[i:i+len(wanted)] == wanted
                               for i in range(len(lines)-len(wanted)+1))


def send(connection, controls, value, timeout=10, on_write=None):
    from send_geometry import button_point
    # controls must resolve and verify the original session every time.
    root, field, button, messages = controls()
    button_point(connection, root, button)
    iface = field.get_text_iface()
    editable = field.get_editable_text_iface()
    if iface is None or editable is None or iface.get_text(0, -1):
        raise RuntimeError('原会话输入框不可写或有人工草稿，未覆盖、未发送。')
    count = sum(matches(row, value) for row in rows(messages))
    if on_write:
        on_write()
    if not editable.set_text_contents(value) or iface.get_text(0, -1) != value:
        raise RuntimeError('正文写入或回读失败，未点击。')
    root, field, button, messages = controls()
    if field.get_text_iface().get_text(0, -1) != value:
        raise RuntimeError('原会话草稿发生变化，未点击。')
    if sum(matches(row, value) for row in rows(messages)) != count:
        raise RuntimeError('点击前相同文字的消息数量已变化，停止且不重试。')
    point = button_point(connection, root, button)
    connection.move(*point)
    root, field, button, _ = controls()
    if (button_point(connection, root, button) != point
            or field.get_text_iface().get_text(0, -1) != value):
        raise RuntimeError('虚拟指针移动后目标或正文变化，未点击。')
    connection.click(1)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, field, _, messages = controls()
        if (sum(matches(row, value) for row in rows(messages)) > count
                and field.get_text_iface().get_text(0, -1) == ''):
            return {'ui_readback': True, 'transport': 'virtual_cursor'}
        time.sleep(0.2)
    raise RuntimeError('虚拟指针已尝试点击，但未观察到原会话新正文及草稿清空；不重试。')
