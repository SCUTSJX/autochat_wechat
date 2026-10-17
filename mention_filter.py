"""Exact display-name mention filtering before group messages enter the queue."""
import re
from group_text import message_text


def wants_reply(event, policy='all', names=()):
    group = (event.get('conversation_kind') == 'group'
             or str(event.get('conversation_id', '')).endswith('@chatroom'))
    if not group or policy == 'all':
        return True
    if policy != 'mention_self':
        return False
    text = message_text(dict(event, conversation_kind='group'))
    for name in names:
        name = name.strip()
        if not name:
            continue
        pattern = r'(?<![A-Za-z0-9_@])@' + re.escape(name) + r'(?=$|[\s，。！？、：；,!?;:])'
        if re.search(pattern, text):
            return True
    return False
