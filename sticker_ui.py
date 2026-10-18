"""Ownership-checked cleanup of task-created input drafts."""


def clear_owned_draft(controls, expected):
    """Clear only an exact task-owned text draft; always read it back."""
    try:
        _, field, _, _ = controls()
        text = field.get_text_iface()
        editable = field.get_editable_text_iface()
        if text is None or editable is None:
            return 'unavailable'
        value = text.get_text(0, -1)
        if value == '':
            return 'empty'
        if expected is None or value != expected:
            return 'changed'
        if not editable.set_text_contents(''):
            return 'failed'
        _, field, _, _ = controls()
        return 'cleared' if field.get_text_iface().get_text(0, -1) == '' else 'failed'
    except Exception:
        return 'failed'
