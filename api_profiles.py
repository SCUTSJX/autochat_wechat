"""Private API profiles and atomic hot switching."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
import uuid
from project_setup import ensure_defaults

FILENAME = 'api-profiles.json'
CHANNELS = ('text', 'vision')


class ProfileError(ValueError):
    pass


class Conflict(ProfileError):
    pass


def is_opencode_url(url):
    """Legacy defaults only; an explicit saved switch always takes precedence."""
    try:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
    except (ValueError, TypeError):
        return False
    return host == 'opencode.ai' or host.endswith('.opencode.ai')


def session_headers(profile, url, session):
    """Return only headers enabled for this profile, never a global default."""
    enabled = profile.get('opencode_session', False) if profile is not None else is_opencode_url(url)
    return {'x-opencode-session': str(session)} if enabled is True else {}


def read_json(path):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ProfileError('配置路径不接受符号链接。')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise ProfileError('无法读取配置，请检查文件格式和权限。') from None
    if not isinstance(value, dict):
        raise ProfileError('配置必须为 JSON 对象。')
    return value


def validate_profile(value, channel, require_key=False):
    if channel not in CHANNELS or not isinstance(value, dict):
        raise ProfileError('接口类型或方案格式无效。')
    result = {}
    for key, limit in (('id',64),('name',80),('base_url',2000),('model',200),('api_key',8192)):
        text = value.get(key, '')
        if not isinstance(text,str) or len(text) > limit or any(ord(c)<32 for c in text):
            raise ProfileError('配置字段格式或长度无效。')
        result[key] = text.strip()
    if not all(result[key] for key in ('id','name','base_url','model')):
        raise ProfileError('请填写方案名称、接口地址和模型。')
    try:
        url = urlsplit(result['base_url'])
        valid = (url.scheme == 'https' and url.hostname and not url.username and not url.password
                 and not url.query and not url.fragment and url.port != 0)
    except ValueError:
        valid = False
    if not valid or any(c.isspace() for c in result['base_url']):
        raise ProfileError('接口请使用 HTTPS 地址，不含账号、查询参数或片段。')
    result['base_url'] = result['base_url'].rstrip('/')
    # Older saved profiles did not have a switch; retain known OpenCode only.
    enabled = value.get('opencode_session', is_opencode_url(result['base_url']))
    if type(enabled) is not bool:
        raise ProfileError('OpenCode session 开关必须为布尔值。')
    result['opencode_session'] = enabled
    if any(c.isspace() for c in result['api_key']) or (require_key and not result['api_key']):
        raise ProfileError('请填写有效 API 密钥；留空仅用于保留已保存的密钥。')
    for key, low, high in (('timeout_seconds',1,600),('max_tokens',1200 if channel=='vision' else 1,
                                                   40000 if channel=='vision' else 128000)):
        number = value.get(key)
        if type(number) is not int or not low <= number <= high:
            raise ProfileError('超时或输出额度超出允许范围。')
        result[key] = number
    return result


def validate_state(data):
    if (not isinstance(data,dict) or data.get('version') != 1
            or type(data.get('revision')) is not int or data['revision'] < 0
            or not isinstance(data.get('channels'),dict) or set(data['channels']) != set(CHANNELS)):
        raise ProfileError('API 方案文件格式无效。')
    for channel in CHANNELS:
        entry = data['channels'][channel]
        if not isinstance(entry,dict) or not isinstance(entry.get('profiles'),list) or not 1 <= len(entry['profiles']) <= 50:
            raise ProfileError('每个接口需保留 1 到 50 个方案。')
        profiles = [validate_profile(item,channel) for item in entry['profiles']]
        ids = [item['id'] for item in profiles]
        if len(set(ids)) != len(ids) or entry.get('active') not in ids:
            raise ProfileError('正在使用的 API 方案不存在或有重复编号。')
        entry['profiles'] = profiles
    return data


def active_profile(root, channel, require_key=True):
    path = Path(root)/FILENAME
    if not path.exists() and not path.is_symlink():
        return None
    data = validate_state(read_json(path))
    entry = data['channels'][channel]
    profile = next(item for item in entry['profiles'] if item['id']==entry['active'])
    if require_key and not profile['api_key']:
        raise ProfileError('当前 API 方案尚未填写密钥，请打开 API 切换面板。')
    return dict(profile)


def env_value(root, name, vision=False):
    if os.environ.get(name):
        return os.environ[name]
    for filename in (('.env.vision','.env') if vision else ('.env',)):
        path = root/filename
        if path.exists():
            if path.is_symlink():
                raise ProfileError('密钥文件不接受符号链接。')
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.partition('=')
                if sep and key.strip()==name:
                    return value.strip().strip('\"\'')
    return ''


def private_json(path, data):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    descriptor = os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary),str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


class Profiles:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if self.root.resolve()!=self.root or not self.root.is_dir():
            raise ProfileError('请选择实际项目目录，不使用链接目录。')
        ensure_defaults(self.root)
        self.path = self.root/FILENAME
        self.lock = threading.RLock()

    @contextmanager
    def writing(self):
        with self.lock:
            path = self.root/'api-profiles.lock'
            if path.is_symlink():
                raise ProfileError('配置锁不能为链接。')
            fd = os.open(str(path),os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'r+b') as stream:
                if os.name=='nt':
                    import msvcrt
                    if os.fstat(stream.fileno()).st_size == 0:
                        stream.write(b'0'); stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name=='nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def imported(self):
        config = read_json(self.root/'config.json')
        tuning = read_json(self.root/'reply-tuning.json') if (self.root/'reply-tuning.json').exists() else {}
        visual = read_json(self.root/'vision-image-test.json').get('vision',{}) if (self.root/'vision-image-test.json').exists() else {}
        text = config['api']
        options = tuning.get('api_options',config.get('api_options',{}))
        visual = visual or dict(text,api_key_env='AI_API_KEY',max_tokens=40000)
        channels = {}
        for channel, api in (('text',text),('vision',visual)):
            identity = 'imported-'+channel
            profile = dict(id=identity,name='原有文字配置' if channel=='text' else '原有图片配置',
                base_url=api.get('base_url') or text['base_url'],model=api.get('model') or text['model'],
                opencode_session=api.get('opencode_session', is_opencode_url(api.get('base_url') or text['base_url'])),
                timeout_seconds=int(api.get('timeout_seconds',90)),
                max_tokens=(max(1200,min(40000,int(api.get('max_tokens',40000)))) if channel=='vision'
                            else int(options.get('max_tokens',8000))),
                api_key=env_value(self.root,visual.get('api_key_env','VISION_API_KEY'),True) if channel=='vision'
                        else env_value(self.root,'AI_API_KEY'))
            channels[channel] = dict(active=identity,profiles=[profile])
        return validate_state(dict(version=1,revision=0,channels=channels))

    def save(self, data):
        """Atomically save private profiles without retaining older keys."""
        data = validate_state(data)
        if self.path.is_symlink():
            raise ProfileError('API 方案文件不能为链接。')
        private_json(self.path, data)

    def ensure(self):
        with self.writing():
            if self.path.exists() or self.path.is_symlink():
                return validate_state(read_json(self.path))
            data = self.imported()
            self.save(data)
            return data

    def public(self):
        data = copy.deepcopy(self.ensure())
        for entry in data['channels'].values():
            for profile in entry['profiles']:
                profile['has_key'] = bool(profile.pop('api_key'))
        return data

    def candidate(self, data, channel, fields, reserve=False):
        if channel not in CHANNELS or not isinstance(fields,dict):
            raise ProfileError('接口类型或表单无效。')
        entry = data['channels'][channel]
        original = next((p for p in entry['profiles'] if p['id']==fields.get('id')),None)
        if fields.get('id') and original is None:
            raise ProfileError('方案已不存在，请刷新。')
        value = dict(fields,id=uuid.uuid4().hex if reserve or not original else original['id'])
        if 'opencode_session' not in value:
            value['opencode_session'] = original['opencode_session'] if original else False
        if not value.get('api_key') and original:
            value['api_key'] = original['api_key']
        return validate_profile(value,channel,require_key=True)

    def mutate(self, command):
        with self.writing():
            data = validate_state(read_json(self.path))
            if command.get('revision')!=data['revision']:
                raise Conflict('配置已更新，请刷新面板后重试。')
            channel, action = command.get('channel'),command.get('action')
            if channel not in CHANNELS:
                raise ProfileError('接口类型无效。')
            entry = data['channels'][channel]
            if action in ('apply','reserve'):
                value = self.candidate(data,channel,command.get('profile'),reserve=action=='reserve')
                entry['profiles'] = [p for p in entry['profiles'] if p['id']!=value['id']]+[value]
                if action=='apply':
                    entry['active'] = value['id']
            elif action=='delete':
                identity = command.get('id')
                if identity==entry['active']:
                    raise ProfileError('正在使用的方案不能删除，请先切换。')
                if not any(p['id']==identity for p in entry['profiles']):
                    raise ProfileError('方案已不存在。')
                entry['profiles'] = [p for p in entry['profiles'] if p['id']!=identity]
            else:
                raise ProfileError('操作无效。')
            data['revision'] += 1
            self.save(data)
        return self.public()

    def test_candidate(self, command):
        data = self.ensure()
        if command.get('revision')!=data['revision']:
            raise Conflict('配置已更新，请刷新面板后重试。')
        return self.candidate(data,command.get('channel'),command.get('profile'))
