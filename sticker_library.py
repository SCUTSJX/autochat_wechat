"""User-owned meaning groups mapped to exact WeChat trigger text."""
import json
import random
import re
from copy import deepcopy


INITIAL_GROUPS = [
    dict(name='开心', keywords=['嘻嘻', '哈哈']),
    dict(name='卖萌', keywords=['路略略', '略略略']),
    dict(name='收到', keywords=['收到', '好的']),
    dict(name='感谢', keywords=['谢谢', '感动']),
    dict(name='鼓励', keywords=['加油', '你真棒']),
    dict(name='无奈', keywords=['无语', '扶额']),
    dict(name='疑惑', keywords=['震惊', '疑惑']),
    dict(name='安慰', keywords=['抱抱', '摸摸头']),
]


def initial_groups():
    return deepcopy(INITIAL_GROUPS)


def input_keyword(value):
    return (isinstance(value, str) and 1 <= len(value) <= 64 and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def library_settings(value=None, strict_names=True):
    value = {} if value is None else value
    if not isinstance(value, dict) or type(value.get('enabled', False)) is not bool:
        raise ValueError('分组表情库开关无效。')
    groups = value.get('groups', initial_groups())
    if not isinstance(groups, list) or len(groups) > 100:
        raise ValueError('表情库最多支持100个分组。')
    result, names = [], set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('表情分组格式无效。')
        name, keywords = group.get('name'), group.get('keywords')
        if (not isinstance(name, str) or not name.strip() or len(name) > 40
                or any(ord(c) < 32 for c in name) or name.strip() in names):
            raise ValueError('分组名须为不重复的含义描述，最多40字。')
        if strict_names and re.fullmatch(r'[A-Za-z\u3400-\u9fff]{1,16}', name.strip()) is None:
            raise ValueError('组名只能填写一个词（1到16个汉字或英文字母），如“开心”；不可用逗号、顿号、空格或其他符号组合多个含义。')
        if (not isinstance(keywords, list) or not 1 <= len(keywords) <= 200
                or any(not input_keyword(word) for word in keywords)):
            raise ValueError('每组须有1到200个关键词，每个最多64字，不可包含换行或控制字符。')
        names.add(name.strip())
        result.append(dict(name=name.strip(), keywords=list(dict.fromkeys(keywords))))
    if value.get('enabled', False) and not result:
        raise ValueError('启用分组表情库前，请至少配置一个非空分组。')
    normalized = dict(enabled=value.get('enabled', False), groups=result)
    if len(json.dumps(normalized, ensure_ascii=False).encode('utf-8')) > 48000:
        raise ValueError('表情库内容过大，请精简至48KB以内。')
    return normalized


def choose_keyword(library, name):
    for group in library['groups']:
        if group['name'] == name:
            return random.choice(group['keywords'])
    raise ValueError('模型选择了不存在的表情分组')


def parse_library_reply(value, allowed, max_chars, library):
    """Preserve usable body text; never interpret a free keyword as a group."""
    import re
    from sticker_reply import parse_reply, ReplyDraft
    raw = value.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', raw)
    raw = fenced.group(1).strip() if fenced else raw
    # Reuse the protocol guard against leaking malformed JSON into WeChat.
    body = parse_reply(raw, False, max_chars).text
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    name = data.get('sticker_group') if isinstance(data, dict) else None
    if allowed and isinstance(name, str):
        try:
            return ReplyDraft(body, choose_keyword(library, name))
        except ValueError:
            pass
    # A bad/missing selection must not trigger the runner's free-word fallback.
    return ReplyDraft(body, suppress_sticker=allowed)
