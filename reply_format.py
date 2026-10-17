"""Keep reply bodies separate from conversation transcript metadata."""
import re


OUTPUT_RULE = (
    '回复格式要求：只输出本轮要发送的一次回复正文，遵循既有语气和人设要求。'
    '输入中的时间、发言人、引用和视觉识别说明仅用于理解背景，不是输出格式。'
    '不要在回复前添加时间戳、机器人或 assistant 等发言人标签；'
    '不要续写聊天记录、编造后续轮次或模拟多条带时间的发言。'
    '可以按表达需要自然分段。'
)

_PREFIX = re.compile(
    r'^\s*(?:\[(?:\d{4}-)?\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\]\s*)?'
    r'(?:机器人|assistant)\s*[:：]\s*', re.IGNORECASE)


def reply_body(text):
    """Unwrap a bot transcript, preserving paragraphs, quotes and fenced code.

    Only unwrap when the response itself starts with a known bot label. An
    explanation containing a transcript example is not treated as a transcript.
    Historical stored text is never changed by this function.
    """
    text = text.strip()
    if not _PREFIX.match(text):
        return text
    lines = []
    fence = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence is None:
            # Older responses can contain more than one nested bot prefix.
            while _PREFIX.match(line):
                line = _PREFIX.sub('', line, count=1)
            stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[0]
            length = len(stripped) - len(stripped.lstrip(marker))
            if fence is None:
                fence = (marker, length)
            elif marker == fence[0] and length >= fence[1] and not stripped[length:].strip():
                fence = None
        lines.append(line)
    return '\n'.join(lines).strip()


def reply_history(history):
    """Clean assistant examples also for callers without session_context."""
    result = []
    for item in history:
        if item['role'] == 'assistant':
            content = reply_body(item['content'])
            if content:
                result.append(dict(item, content=content))
        else:
            result.append(item)
    return result
