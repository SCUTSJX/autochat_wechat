"""Platform-independent reply logic, compatible with Python 3.8+."""
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
import httpx
import atexit
from reply_format import OUTPUT_RULE, reply_body, reply_history
from api_profiles import active_profile, session_headers
from project_setup import ensure_defaults

ROOT = Path(__file__).resolve().parent
SESSION = str(uuid.uuid4())
_CLIENT = None
_THINKING_UNSUPPORTED = set()
FALLBACK_REPLY = '这条我有点卡住了，换个问法再聊？'


def http_client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client()
        atexit.register(_CLIENT.close)
    return _CLIENT




@dataclass(frozen=True)
class NativeMessage:
    """Verified adapter event. Unknown metadata must keep default values.

    conversation_id is the internal destination, never a UI title or last chat.
    The native adapter is still pending; this type does not parse raw records.
    """
    message_id: str
    conversation_id: str
    text: str
    direction: str
    conversation_kind: str = "unknown"
    message_kind: str = "unknown"
    is_friend: bool = False
    is_new: bool = False
    conversation_verified: bool = False
    conversation_name: str = ""


def reply_destination(config, message):
    """Return the verified original destination or None; never infer a chat."""
    if (not message.message_id or not message.conversation_id
            or not message.conversation_verified or not message.is_new
            or message.direction != "incoming" or message.message_kind != "text"
            or not message.text.strip()):
        return None
    if message.conversation_kind in ('private', 'group'):
        return message.conversation_id
    return None






def settings():
    ensure_defaults(ROOT)
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    tuning_path = ROOT/'reply-tuning.json'
    if tuning_path.exists():
        tuning = json.loads(tuning_path.read_text(encoding='utf-8-sig'))
        allowed = {'min_reply_interval_seconds', 'context_turns', 'max_reply_chars',
                   'visible_context_rows', 'visible_context_chars', 'context_input_chars',
                   'api_options', 'timing_log', 'context_messages_limit', 'media_wait_seconds',
                   'vision_concurrency'}
        config.update({key:value for key,value in tuning.items() if key in allowed})
    prompt_path = ROOT/'system-prompt.txt'
    if prompt_path.exists():
        config['system_prompt'] += '\n\n' + prompt_path.read_text(encoding='utf-8-sig').strip()
    mentions_path = ROOT/'group-mentions.json'
    if mentions_path.exists():
        mentions = json.loads(mentions_path.read_text(encoding='utf-8-sig'))
        policy, names = mentions.get('group_reply_policy'), mentions.get('self_mention_names', [])
        if policy not in ('all', 'mention_self') or not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError('群聊 @ 回复配置无效。')
        if policy == 'mention_self' and not any(name.strip() for name in names):
            raise ValueError('请在 group-mentions.json 填入 @你时显示的完整群昵称。')
        config.update(group_reply_policy=policy, self_mention_names=names)
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    if config["poll_seconds"] <= 0 or config["context_turns"] < 1:
        raise ValueError("轮询间隔和上下文轮数必须大于零。")
    return config




def bounded_history(history, budget):
    selected = []
    for item in reversed(list(history)):
        value = item['content']
        if len(value) > budget:
            break
        selected.append(item)
        budget -= len(value)
    return list(reversed(selected))


def request_snapshot(path, body):
    """Private exact JSON body only; never persist Authorization headers."""
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(body, stream, ensure_ascii=False, indent=2)
    if hasattr(os, 'chown') and os.geteuid() == 0 and os.environ.get('SUDO_UID'):
        os.chown(temporary, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
    os.replace(temporary, path)


def fallback_reply(config, reason, payload=None):
    """Return sendable text without exposing provider bodies or reasoning."""
    finish = None
    if isinstance(payload, dict):
        choices = payload.get('choices')
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish = choices[0].get('finish_reason')
    known = ('stop', 'length', 'content_filter', 'tool_calls', 'function_call', 'insufficient_system_resource')
    detail = '；finish_reason='+finish if isinstance(finish, str) and finish in known else ''
    print('文字 AI 回复不可用（'+reason+detail+'），已改用简短兜底正文。', flush=True)
    return FALLBACK_REPLY[:max(1, int(config['max_reply_chars']))]


def post_reply(client, url, headers, body, timeout, config):
    """Only network/API failures are recoverable here; local I/O stays outside."""
    try:
        return client.post(url, headers=headers, json=body, timeout=timeout), None
    except (OSError, getattr(httpx, 'HTTPError', OSError)):
        return None, fallback_reply(config, '接口请求失败或超时')


def reply(config, history, text, request_path=None):
    api = dict(config["api"])
    profile = active_profile(ROOT, 'text')
    if profile:
        api.update({name:profile[name] for name in ('base_url','model','timeout_seconds')})
    key = profile['api_key'] if profile else os.environ.get("AI_API_KEY", "")
    if not key:
        raise RuntimeError("请在 .env 填入 AI_API_KEY。")
    base = api["base_url"].rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("API 必须使用 HTTPS。")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    options = dict(config.get('api_options', {}))
    if profile:
        options['max_tokens'] = profile['max_tokens']
    unsupported_key = (url, api['model'])
    if unsupported_key in _THINKING_UNSUPPORTED:
        options.pop('thinking', None)
        options.pop('max_tokens', None)
    body = dict(options, model=api['model'], stream=False,
                messages=[{'role':'system', 'content':config['system_prompt']+'\n\n'+OUTPUT_RULE}]
                         + bounded_history(reply_history(history), config.get('context_input_chars', 6000))
                         + [{'role':'user', 'content':text}])
    headers = {"Authorization": "Bearer " + key,
        "User-Agent": "wechat-auto-reply-ubuntu/1.0"}
    legacy = {'opencode_session':api['opencode_session']} if 'opencode_session' in api else None
    headers.update(session_headers(profile if profile is not None else legacy, url, SESSION))
    client = http_client()
    if request_path is not None:
        request_snapshot(request_path, body)
    response, fallback = post_reply(client, url, headers, body, api.get('timeout_seconds', 45), config)
    if fallback is not None:
        return fallback
    if (response.status_code == 400 and 'thinking' in body
            and 'thinking' in response.text.lower()):
        _THINKING_UNSUPPORTED.add(unsupported_key)
        body.pop('thinking', None)
        body.pop('max_tokens', None)
        print('接口未接受关闭思考参数，已使用兼容模式；本次运行不再重复尝试该参数。', flush=True)
        if request_path is not None:
            request_snapshot(request_path, body)
        response, fallback = post_reply(client, url, headers, body, api.get('timeout_seconds', 45), config)
        if fallback is not None:
            return fallback
    try:
        response.raise_for_status()
    except (OSError, getattr(httpx, 'HTTPError', OSError)):
        status = response.status_code if type(response.status_code) is int else 'unknown'
        return fallback_reply(config, '接口 HTTP '+str(status))
    payload = None
    try:
        payload = response.json()
        result = payload["choices"][0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return fallback_reply(config, '接口返回格式异常', payload)
    if isinstance(result, list):
        result = '\n'.join(item['text'] for item in result
                           if isinstance(item, dict) and item.get('type') == 'text'
                           and isinstance(item.get('text'), str))
    if not isinstance(result, str) or not result.strip():
        return fallback_reply(config, '未返回有效正文', payload)
    result = reply_body(result)
    if not result:
        return fallback_reply(config, '仅返回发言标签', payload)
    return result[:config["max_reply_chars"]]
