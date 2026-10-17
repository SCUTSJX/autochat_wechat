"""Frida receive transport; refuses unverified record layouts."""
import hashlib
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from core import ROOT
from mention_filter import wants_reply


def current_wechat_pid():
    owner = int(os.environ.get("SUDO_UID", str(os.getuid())))
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid == owner and (proc / 'exe').resolve(strict=True).name == 'wechat':
                found.append(int(proc.name))
        except (OSError, RuntimeError):
            continue
    if len(found) != 1:
        raise RuntimeError("未找到当前桌面用户唯一的微信进程；请登录微信，关闭重复实例。")
    return found[0]


def process_start_ticks(pid):
    value=(Path('/proc')/str(pid)/'stat').read_text(encoding='utf-8')
    return int(value.rsplit(')',1)[1].split()[19])


def validate_layout(profile):
    layout = profile.get('receiver_layout', {})
    expected = {'stride':632, 'type_offset':12, 'from_offset':24,
                'to_offset':48, 'server_id_offset':248}
    if not layout.get('verified') or any(layout.get(k) != v for k, v in expected.items()):
        raise RuntimeError("接收记录布局尚未实测验证；请先完成指定测试文字/会话字段验证。")
    if layout.get('body_offset') not in (72, 304):
        raise RuntimeError("候选正文字段未确认。")
    if (not layout.get('stable_id_verified') or not layout.get('conversation_fields_verified')
            or not layout.get('create_time_verified') or layout.get('create_time_offset') != 276):
        raise RuntimeError("稳定消息 ID、原始会话字段或创建时间尚未验证。")
    return layout


def validate_candidate(profile):
    """Only the observed build/layout may be used for interactive calibration."""
    layout = profile.get('receiver_layout', {})
    expected = {'stride':632, 'type_offset':12, 'from_offset':24,
                'to_offset':48, 'body_offset':304, 'server_id_offset':248,
                'create_time_offset':276}
    if (profile.get('sha256') != '2749535f299ba67feb8ef80cab898bb0531fa8f376b16f8a72db706811c84132'
            or not layout.get('body_verified')
            or any(layout.get(k) != v for k,v in expected.items())
            or not profile.get('direction_accessor')):
        raise RuntimeError('校准仅支持已观察的 Linux 微信 4.1.13.26 记录布局。')
    return layout


class Receiver:
    def __init__(self, pid, profile, calibration=False):
        if not sys.platform.startswith('linux') or os.geteuid() != 0:
            raise RuntimeError("原生接收默认要求 root；请使用 reply_all.sh，通过 sudo 启动。")
        if calibration:
            validate_candidate(profile)
            profile = dict(profile, calibration_mode=True)
        else:
            validate_layout(profile)
        proc = Path('/proc') / str(pid)
        if proc.stat().st_uid != int(os.environ.get('SUDO_UID', str(os.getuid()))):
            raise RuntimeError("只允许接入当前登录用户的微信进程。")
        binary = (proc/'exe').resolve(strict=True)
        digest = hashlib.sha256()
        with binary.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
        if digest.hexdigest() != profile['sha256']:
            raise RuntimeError("微信版本/文件指纹已变化，拒绝使用旧偏移。")
        self.reply_queue_limit = profile.get('reply_queue_limit')
        self.accept_images = profile.get('accept_images') is True and not calibration
        self.accept_nontext = profile.get('accept_nontext') is True and not calibration
        self.accept_own_messages = profile.get('accept_own_messages') is True and not calibration
        self.record_all_messages = profile.get('record_all_messages') is True and not calibration
        self.group_reply_policy = profile.get('group_reply_policy', 'all')
        self.self_mention_names = profile.get('self_mention_names', [])
        if self.reply_queue_limit is not None and (type(self.reply_queue_limit) is not int or self.reply_queue_limit != 10):
            raise ValueError('回复等待队列必须为 10 条。')
        self.events = queue.Queue(maxsize=self.reply_queue_limit or 256)
        self.seen_messages = set()
        self.dropped_messages = 0
        self.failure = None
        self.ready = threading.Event()
        self.session = self.script = None
        self.closing = False
        source = (ROOT/'native_receive.js').read_text(encoding='utf-8')
        source = source.replace('__PROFILE__', json.dumps(profile)).replace('__BINARY__', json.dumps(str(binary)))
        try:
            import frida
            self.session = frida.get_local_device().attach(pid)
            self.session.on('detached', self._detached)
            self.script = self.session.create_script(source)
            self.script.on('message', self._message)
            self.script.load()
            if not self.ready.wait(10):
                raise RuntimeError(self.failure or "原生接收端未就绪。")
            if self.failure:
                raise RuntimeError(self.failure)
        except Exception as exc:
            self.close()
            if type(exc).__name__ == 'ProcessNotFoundError':
                raise RuntimeError("微信进程已退出或 PID 变化；重新登录后通过启动器自动识别。") from None
            if type(exc).__name__ == 'PermissionDeniedError':
                raise RuntimeError("root 挂接仍被系统拒绝；检查虚拟机安全策略及进程隔离，未修改系统策略。") from None
            if isinstance(exc, ImportError):
                raise RuntimeError("缺少 Frida；请按 README.md 安装收发依赖。") from None
            raise

    def _detached(self, *args):
        if not self.closing:
            self.failure = "微信进程退出或挂接中断；自动回复停止。"
            self.ready.set()

    def _message(self, message, data):
        if message.get('type') == 'error':
            self.failure = "原生接收脚本异常；停止且不输出进程数据。"
            self.ready.set()
            return
        payload = message.get('payload', {})
        if payload.get('type') == 'ready':
            self.ready.set()
        elif payload.get('type') == 'fatal':
            self.failure = "消息记录不符合已验证布局，停止。"
            self.ready.set()
        elif payload.get('type') == 'message':
            if self.reply_queue_limit:
                kind = payload.get('message_kind')
                accepted = (kind == 'text' or (kind == 'image' and self.accept_images)
                            or (kind == 'nontext' and self.accept_nontext))
                direction_ok = (payload.get('direction') == 'incoming'
                                or (self.accept_own_messages and payload.get('direction') == 'outgoing'))
                if not direction_ok or not accepted:
                    return
                if kind == 'text' and not getattr(self, 'record_all_messages', False) and not wants_reply(payload, getattr(self, 'group_reply_policy', 'all'),
                                   getattr(self, 'self_mention_names', [])):
                    return
                key = (payload.get('conversation_id'), payload.get('message_id'))
                if key in self.seen_messages:
                    return
                self.seen_messages.add(key)
                if len(self.seen_messages) > 100000:
                    self.failure = '本次去重记录已达上限，请重新启动。'
                    return
            try:
                self.events.put_nowait(dict(payload, received_monotonic=time.monotonic()))
            except queue.Full:
                if self.reply_queue_limit:
                    self.dropped_messages += 1
                    print('等待队列已满 10 条，跳过新消息，不补发。累计跳过：%d'
                          % self.dropped_messages, flush=True)
                else:
                    self.failure = "消息队列已满，停止以避免丢消息或跨会话回复。"

    def receive(self, timeout=1):
        if self.failure:
            raise RuntimeError(self.failure)
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.closing = True
        script, session = self.script, self.session
        self.script = self.session = None
        for item, action in ((script,'unload'),(session,'detach')):
            if item:
                try:
                    getattr(item,action)()
                except Exception:
                    print("挂接清理未能确认；请退出并重新打开微信。",file=sys.stderr)

