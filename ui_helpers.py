"""Shared accessibility helpers for visible chat controls."""
from atspi_backend import bounds, children

def visible(node):
    try:
        rect = bounds(node)
        return rect[2] > 0 and rect[3] > 0
    except Exception:
        return False

def unique(nodes, description):
    if len(nodes) != 1:
        raise RuntimeError(description+'不是唯一可见控件，停止。')
    return nodes[0]

def rows(messages):
    return [n.get_name() or '' for n in children(messages) if n]
