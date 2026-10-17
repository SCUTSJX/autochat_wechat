"""Bounded group metadata normalization; private messages remain unchanged."""
import re


def message_text(event):
    text = event['text']
    if event.get('conversation_kind') != 'group':
        return text
    first, separator, body = text.partition('\n')
    if not separator or not first.endswith((':' , ':\r')):
        return text
    sender = first.rstrip('\r')[:-1]
    from_id = event.get('from_id', '')
    if ((from_id and not from_id.endswith('@chatroom') and sender == from_id)
            or re.fullmatch(r'wxid_[A-Za-z0-9_]+', sender)):
        return body if body.strip() else text
    return text


def title_name(value):
    # Compare a group title with/without its trailing member count.
    return re.sub(r'\s*[（(]\d+[)）]\s*$', '', value).strip()
