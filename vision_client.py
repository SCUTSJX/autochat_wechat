"""Image conversion, private output and vision API requests."""
import base64
import hashlib
import io
import os
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parent
SESSION = uuid.uuid4()

class MatchError(RuntimeError):
    def __init__(self, status, candidates=None):
        super().__init__(status)
        self.status = status
        self.candidates = candidates or []

def jpg_bytes(path, expected, max_bytes):
    from PIL import Image, ImageOps
    before = path.stat()
    if (before.st_size, before.st_mtime_ns) != expected or before.st_size > max_bytes:
        raise MatchError('cache_changed')
    with path.open('rb') as stream:
        data = stream.read(max_bytes+1)
    after = path.stat()
    if len(data) > max_bytes or (after.st_size, after.st_mtime_ns) != expected:
        raise MatchError('cache_changed')
    # Decode the file, regardless of .dat extension. Encrypted content fails here.
    with Image.open(io.BytesIO(data)) as image:
        if image.width*image.height > 40000000:
            raise MatchError('image_dimensions_too_large')
        image.load()
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
            rgba = image.convert('RGBA')
            rgb = Image.new('RGB', image.size, 'white')
            rgb.paste(rgba, mask=rgba.getchannel('A'))
        else:
            rgb = image.convert('RGB')
        buffer = io.BytesIO()
        rgb.save(buffer, 'JPEG', quality=95)
    return buffer.getvalue(), width, height, hashlib.sha256(data).hexdigest()

def env_key(name):
    if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name or ''):
        raise ValueError('api_key_env 无效')
    value = os.environ.get(name, '')
    if value:
        return value
    for env in (ROOT/'.env.vision', ROOT/'.env'):
        if env.exists():
            for line in env.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.partition('=')
                if sep and key.strip() == name:
                    return value.strip().strip('\"\'')
    return ''

def describe(vision, image, client=None, conversation_id=None):
    from api_profiles import active_profile
    profile = active_profile(ROOT, 'vision')
    vision = dict(vision)
    if profile:
        vision.update({name:profile[name] for name in ('base_url','model','timeout_seconds','max_tokens')})
    key = profile['api_key'] if profile else env_key(vision.get('api_key_env', 'VISION_API_KEY'))
    if not vision.get('base_url') or not vision.get('model') or not key:
        return {'status': 'vision_not_configured'}
    base = vision['base_url'].rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('视觉接口地址必须使用 HTTPS')
    url = base if base.endswith('/chat/completions') else base+'/chat/completions'
    body = {'model': vision['model'], 'stream': False,
            'max_tokens': vision.get('max_tokens', 512),
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': vision.get('prompt') or '简单描述这个图片是什么'},
                {'type': 'image_url', 'image_url': {
                    'url': 'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii')}}]}]}
    if vision.get('reasoning_effort'):
        body['reasoning_effort'] = vision['reasoning_effort']
    enabled = profile['opencode_session'] if profile else vision.get('opencode_session')
    if client is None:
        import httpx
        with httpx.Client() as session:
            return request_vision(session, url, key, body, vision.get('timeout_seconds', 60), conversation_id,
                                  opencode_session=enabled)
    return request_vision(client, url, key, body, vision.get('timeout_seconds', 60), conversation_id,
                          opencode_session=enabled)

def request_vision(client, url, key, body, timeout, conversation_id=None, opencode_session=None):
    from api_profiles import session_headers
    # If enabled, use an anonymous stable value; never send the raw WeChat ID.
    session = str(uuid.uuid5(SESSION, str(conversation_id))) if conversation_id else str(SESSION)
    headers = {'Authorization': 'Bearer '+key, 'User-Agent': 'wechat-auto-reply-ubuntu/1.0'}
    selected = {'opencode_session':opencode_session} if opencode_session is not None else None
    headers.update(session_headers(selected, url, session))
    response = client.post(url, headers=headers, json=body, timeout=timeout)
    # Avoid logging provider response bodies which may echo private request data.
    if response.status_code >= 400:
        raise RuntimeError('视觉接口返回 HTTP %d' % response.status_code)
    content = response.json()['choices'][0]['message']['content']
    if isinstance(content, list):
        content = '\n'.join(item.get('text', '') for item in content if item.get('type') == 'text')
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('视觉模型未返回有效文字')
    return {'status': 'vision_replied', 'answer': content.strip()}

def own(path):
    if hasattr(os, 'chown') and os.geteuid() == 0 and os.environ.get('SUDO_UID'):
        os.chown(path, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))

def private_file(path, data):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        own(path)
        stream.write(data)
