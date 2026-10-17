"""Default configuration and separate public-layout/private-account profiles."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent
DEFAULT_FILES = {'config.json', 'reply-tuning.json', 'group-mentions.json',
                 'vision-image-test.json', 'system-prompt.txt'}


def read_object(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('配置文件不能是符号链接。')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('配置必须为 JSON 对象。')
    return value


def write_private(path, value, overwrite=True):
    path = Path(path)
    if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
        raise ValueError('配置路径不能是符号链接。')
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            if hasattr(os, 'fchown') and os.geteuid() == 0 and os.environ.get('SUDO_UID'):
                os.fchown(stream.fileno(), int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(str(temporary), str(path))
        else:
            # A concurrent first launch must never replace a user's configuration.
            os.link(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def ensure_defaults(root=ROOT):
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError('项目目录不能经过符号链接。')
    defaults = read_object(root / 'defaults.json')
    if set(defaults) != DEFAULT_FILES:
        raise ValueError('默认配置清单无效。')
    created = []
    for name in sorted(DEFAULT_FILES):
        path = root / name
        if path.is_symlink():
            raise ValueError('本地配置不能是符号链接。')
        if not path.exists():
            try:
                write_private(path, defaults[name], overwrite=False)
                created.append(name)
            except FileExistsError:
                pass
    return created


def layout_digest(layout):
    return hashlib.sha256(json.dumps(layout, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def account_record(layout, own):
    if not isinstance(own, str) or not own.strip() or len(own) > 256 or own.endswith('@chatroom'):
        raise ValueError('校准账号身份无效。')
    return dict(version=1, layout_sha256=layout_digest(layout), self_id=own,
                ui_challenge_match=True, timestamp_check=True, send_verified=True)


def load_profile(root=ROOT):
    root = Path(root)
    layout = read_object(root / 'wechat-layout.json')
    account_path = root / 'account-calibration.json'
    if account_path.exists() or account_path.is_symlink():
        account = read_object(account_path)
        if (account.get('version') != 1 or account.get('layout_sha256') != layout_digest(layout)
                or not isinstance(account.get('self_id'), str) or not account['self_id'].strip()
                or any(account.get(k) is not True for k in ('ui_challenge_match', 'timestamp_check', 'send_verified'))):
            raise ValueError('账号校准无效或版本适配已变更，请运行 bash reply_all.sh init。')
    else:
        # Preserve the owner's working setup, but never put this legacy file in a release.
        legacy_path = root / 'calibrated-receiver.json'
        if not legacy_path.exists():
            raise ValueError('尚未校准当前账号，请运行 bash reply_all.sh init。')
        legacy = read_object(legacy_path)
        proof = legacy.get('calibration_proof', {})
        if (legacy.get('sha256') != layout.get('sha256') or not proof.get('self_id')
                or proof.get('ui_challenge_match') is not True or proof.get('timestamp_check') is not True):
            raise ValueError('旧账号校准无效，请运行 bash reply_all.sh init。')
        account = account_record(layout, proof['self_id'])
    profile = copy.deepcopy(layout)
    profile['calibration_proof'] = {k: account[k] for k in ('self_id', 'ui_challenge_match', 'timestamp_check')}
    return profile


def reply_options(root=ROOT):
    path = Path(root) / 'group-mentions.json'
    value = read_object(path)
    return dict(group_reply_policy=value.get('group_reply_policy', 'all'),
                self_mention_names=value.get('self_mention_names', []),
                revision=hashlib.sha256(path.read_bytes()).hexdigest())


def save_reply_options(root, command):
    current = reply_options(root)
    if command.get('revision') != current['revision']:
        raise ValueError('回复策略已被其他窗口修改，请刷新后重试。')
    policy = command.get('group_reply_policy')
    names = command.get('self_mention_names')
    if (policy not in ('all', 'mention_self') or not isinstance(names, list) or len(names) > 30
            or any(not isinstance(n, str) or not n.strip() or len(n) > 80
                   or any(ord(c) < 32 for c in n) for n in names)):
        raise ValueError('回复策略或群昵称格式无效。')
    names = list(dict.fromkeys(n.strip() for n in names))
    if policy == 'mention_self' and not names:
        raise ValueError('仅回复 @ 时，至少填写一个实际显示的完整群昵称。')
    write_private(Path(root) / 'group-mentions.json',
                  dict(group_reply_policy=policy, self_mention_names=names))
    return reply_options(root)


if __name__ == '__main__':
    ensure_defaults()
    print('默认配置已就绪；已有配置保持不变。')
