"""Structured model replies; keywords are data, never keyboard instructions."""
from dataclasses import dataclass
import json
import re

from reply_format import reply_body

KEYWORD = re.compile(r'[\u3400-\u9fff]{1,8}\Z')
OUTPUT_RULE = (
    '回复格式要求：只输出一个 JSON 对象，恰好包含 text 和 sticker_keyword 两个字段。'
    'text 是本轮要发给对方的回复正文，遵循既有人设；不要添加时间、发言人标签或模拟后续对话。'
    '只需发送表情且本轮允许表情时，text 可以为空字符串。'
    'sticker_keyword 是用于微信搜索已添加表情的简短中文词，如嘻嘻、收到、开心；'
    '不是文件路径、操作步骤或正文的一部分，只能是1到8个汉字。'
    '根据本轮发送间隔要求选择符合语境的关键词；未到间隔时为 null。'
    '不输出 Markdown、解释或额外字段。示例：'
    '{"text":"哈哈，明白了。","sticker_keyword":"嘻嘻"}。'
    '历史回复可能是普通正文，仍须按本轮 JSON 格式输出。'
)


@dataclass(frozen=True)
class ReplyDraft:
    text: str
    sticker_keyword: str = None
    fallback: bool = False
    suppress_sticker: bool = False


def valid_keyword(value):
    return isinstance(value, str) and KEYWORD.fullmatch(value) is not None


def parse_reply(value, allowed, max_chars):
    value = value.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', value)
    if fenced:
        value = fenced.group(1).strip()
    try:
        data = json.loads(value)
    except ValueError:
        # Providers occasionally ignore the JSON instruction. Keep a genuine
        # plain answer, but never expose truncated/invalid protocol objects.
        if (not value or value.startswith(('{', '[', '```json')) or
                re.search(r'["\'](?:text|sticker_keyword|sticker_group)["\']\s*:', value)):
            raise ValueError('回复 JSON 不完整或无效') from None
        body = reply_body(value)
        if not body:
            raise ValueError('回复缺少正文')
        return ReplyDraft(body[:max_chars])
    if isinstance(data, str) and reply_body(data):
        return ReplyDraft(reply_body(data)[:max_chars])
    if not isinstance(data, dict) or not {'text', 'sticker_keyword'}.intersection(data):
        raise ValueError('回复结构不完整')
    body = data.get('text', '')
    if body is None:
        body = ''
    if not isinstance(body, str):
        raise ValueError('回复正文必须为文字')
    keyword = data.get('sticker_keyword')
    # Invalid keywords never discard an otherwise useful answer.
    keyword = keyword if allowed and valid_keyword(keyword) else None
    body = reply_body(body)
    if not body and not keyword:
        raise ValueError('回复既无正文也无可发送表情')
    return ReplyDraft(body[:max_chars], keyword)


def requested_sticker(text, mention_names=()):
    """Only exact short commands; never infer a command from a quoted example."""
    value = text.strip()
    for name in mention_names:
        prefix = '@'+name.strip()
        if name.strip() and value.startswith(prefix) and (len(value) == len(prefix) or value[len(prefix)].isspace()):
            value = value[len(prefix):].strip()
    value = re.sub(r'^@\S+\s+', '', value)
    value = re.sub(r'[。！？!?]+$', '', value).strip()
    word = r'["\'“「\[]?([\u3400-\u9fff]{1,8})["\'”」\]]?'
    prefix = r'(?:请)?(?:帮我|给我)?(?:只)?(?:回复|发送|发|回)\s*'
    patterns = (
        prefix+r'(?:一个|个)?\s*'+word+r'(?:的)?表情(?:包)?',
        prefix+r'(?:一个|个)\s*'+word,
        r'(?:发送|发)?表情(?:包)?\s*[:：]\s*'+word,
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value)
        if match:
            keyword = match.group(1)
            if keyword in ('指定', '合适', '适合', '任意', '随机', '某个', '一个', '表情', '表情包',
                           '指定的', '合适的', '适合的', '任意的', '随机的'):
                return None
            return keyword
    return None


def sticker_settings(config):
    value = config.get('stickers', {})
    if not isinstance(value, dict):
        raise ValueError('stickers 必须为对象')
    result = dict(enabled=True, min_replies=3, candidate_timeout_seconds=0.3,
                  send_timeout_seconds=10)
    if set(value) - set(result):
        raise ValueError('stickers 包含未知配置项')
    result.update(value)
    if type(result['enabled']) is not bool:
        raise ValueError('stickers.enabled 必须为布尔值')
    if type(result['min_replies']) is not int or not 1 <= result['min_replies'] <= 1000:
        raise ValueError('stickers.min_replies 必须为1到1000的整数')
    for key in ('candidate_timeout_seconds', 'send_timeout_seconds'):
        if type(result[key]) not in (int, float) or not 0.1 <= result[key] <= 30:
            raise ValueError('表情等待时间必须为0.1到30秒')
    return result
