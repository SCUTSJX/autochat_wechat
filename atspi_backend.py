"""Accessibility primitives used by the current multi-window sender."""
import os
from pathlib import Path

def atspi():
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi
    except (ImportError, ValueError):
        raise RuntimeError("需要 Ubuntu 的 python3-gi 和 gir1.2-atspi-2.0，并使用 --system-site-packages 虚拟环境。") from None

def children(node):
    return [node.get_child_at_index(i) for i in range(node.get_child_count())]

def supports(node, interface):
    return any(str(value).split(".")[-1] == interface for value in node.get_interfaces())

def application():
    api = atspi()
    desktop = api.get_desktop(0)
    apps = []
    for node in children(desktop):
        if not node:
            continue
        try:
            pid = node.get_process_id()
            proc = Path('/proc') / str(pid)
            identified = (proc.stat().st_uid == os.getuid()
                          and (proc/'exe').resolve(strict=True).name == 'wechat')
        except Exception:
            identified = False
        if identified:
            apps.append(node)
    if not apps:
        apps = [n for n in children(desktop) if n and (n.get_name() or "").lower() in ("wechat", "weixin", "微信")]
    if len(apps) != 1:
        raise RuntimeError("未找到唯一 Linux 微信 AT-SPI 应用（找到 %d 个）；请先启动微信并开放辅助功能。" % len(apps))
    return apps[0]

def bounds(node):
    component = node.get_component_iface()
    if component is None:
        raise RuntimeError("控件不提供位置元信息，无法判断消息方向。")
    rect = component.get_extents(atspi().CoordType.SCREEN)
    return (rect.x, rect.y, rect.width, rect.height)

def spec(path, node):
    return {"path": list(path), "role": node.get_role_name(), "name": node.get_name() or ""}

def resolve(root, selector, check_name=True):
    node = root
    for index in selector["path"]:
        if index >= node.get_child_count():
            raise RuntimeError("控件路径已变化，请重新校准。")
        node = node.get_child_at_index(index)
    if node.get_role_name() != selector["role"]:
        raise RuntimeError("控件类型已变化，请重新校准。")
    if check_name and (node.get_name() or "") != selector["name"]:
        raise RuntimeError("控件名称不匹配，请重新校准。")
    return node
