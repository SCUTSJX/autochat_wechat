"""Interactive, account-specific receive and desktop-send verification."""
import argparse
import json
import os
import select
import signal
import subprocess
import sys
import time
import uuid

from project_setup import ROOT, account_record, ensure_defaults, read_object, write_private


def verify_pair(incoming, outgoing, started):
    for event, direction in ((incoming, 'incoming'), (outgoing, 'outgoing')):
        if (event.get('direction') != direction or event.get('message_kind') != 'text'
                or event.get('conversation_kind') != 'private'
                or not str(event.get('message_id', '')).isdecimal()
                or int(event['message_id']) <= 0
                or not started <= event.get('created_at', 0) <= int(time.time()) + 120):
            raise RuntimeError('校准消息方向、类型、ID 或时间校验失败，未保存。')
    peer, own = incoming.get('from_id'), incoming.get('to_id')
    if (not peer or not own or peer == own or peer.endswith('@chatroom')
            or outgoing.get('from_id') != own or outgoing.get('to_id') != peer
            or incoming.get('conversation_id') != peer or outgoing.get('conversation_id') != peer
            or incoming['message_id'] == outgoing['message_id']):
        raise RuntimeError('两条消息的账号或会话身份不一致，未保存。')
    return peer, own


def wait_token(receiver, token, direction, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        event = receiver.receive(min(1, max(0.01, deadline - time.monotonic())))
        if event and event.get('text') == token:
            if event.get('direction') != direction:
                raise RuntimeError('消息收发方向与提示不一致，未保存。')
            return event
    raise RuntimeError('等待校准消息超时，未保存；请重新运行 init。')


def read_worker(worker, seconds=30):
    if not select.select([worker.stdout], [], [], seconds)[0]:
        raise RuntimeError('桌面校验进程超时，未保存。')
    line = worker.stdout.readline()
    if not line:
        raise RuntimeError('桌面校验进程退出，未保存。')
    return json.loads(line)


def finish_calibration(root, layout, own, peer, result):
    if (result.get('ok') is not True or result.get('conversation_id') != peer
            or result.get('ui_readback') is not True or result.get('send_verified') is not True):
        raise RuntimeError('桌面发送回读验证未通过，旧校准保持不变。')
    write_private(root / 'account-calibration.json', account_record(layout, own))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contact', help='测试好友独立窗口中的完整显示名称')
    parser.add_argument('--seconds', type=int, default=180)
    args = parser.parse_args()
    receiver = worker = lock = None
    try:
        if not sys.platform.startswith('linux') or os.geteuid() != 0 or int(os.environ.get('SUDO_UID', '0')) <= 0:
            raise RuntimeError('请从普通用户图形终端运行 bash reply_all.sh init。')
        if not os.environ.get('DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
            raise RuntimeError('账号初始化需要 Xorg 图形桌面，请选择 Ubuntu on Xorg 后重试。')
        if not 30 <= args.seconds <= 600:
            raise ValueError('等待时间应为 30 至 600 秒。')
        ensure_defaults()
        from native_receiver import Receiver, current_wechat_pid, process_start_ticks
        from desktop_user import sender_command
        import fcntl
        lock = (ROOT / 'bot.lock').open('a+')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        layout = read_object(ROOT / 'wechat-layout.json')
        contact = (args.contact or input('请输入测试好友独立窗口中的完整名称：')).strip()
        if not contact or len(contact) > 200 or any(ord(c) < 32 for c in contact):
            raise ValueError('测试好友名称无效。')
        print('本次将核对一条好友来信和一条你从手机发出的消息，随后向该好友发送一次校验文字。')
        print('请使用你有权操作的测试会话，打开其独立窗口、清空输入框，并保持窗口可见。')
        if input('确认进行上述收发校验，请输入 YES：').strip() != 'YES':
            print('已取消，旧校准保持不变。')
            return 1
        pid = current_wechat_pid()
        ticks = process_start_ticks(pid)
        started = int(time.time())
        challenge = uuid.uuid4().hex
        incoming_token, outgoing_token = 'wxinit_in_' + challenge, 'wxinit_own_' + challenge
        receiver = Receiver(pid, dict(layout, calibration_tokens=[incoming_token, outgoing_token]), calibration=True)
        print('请让测试好友发给你下面这整行文字：', flush=True)
        print(incoming_token, flush=True)
        incoming = wait_token(receiver, incoming_token, 'incoming', args.seconds)
        print('请使用你自己的手机微信（同一个账号），向该好友发送下面这整行文字：', flush=True)
        print(outgoing_token, flush=True)
        outgoing = wait_token(receiver, outgoing_token, 'outgoing', args.seconds)
        peer, own = verify_pair(incoming, outgoing, started)
        command = sender_command(int(os.environ['SUDO_UID']))
        command[-1] = str(ROOT / 'virtual_cursor.py')
        subprocess.run(command + ['create'], cwd=str(ROOT), check=True, timeout=30)
        command[-1] = str(ROOT / 'multi_window_sender.py')
        worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding='utf-8', cwd=str(ROOT))
        if read_worker(worker).get('ready') is not True:
            raise RuntimeError('桌面校验进程未就绪。')
        worker.stdin.write(json.dumps(dict(op='init_verify', conversation_id=peer, name=contact,
            incoming=incoming_token, outgoing=outgoing_token), ensure_ascii=False) + '\n')
        worker.stdin.flush()
        result = read_worker(worker, 90)
        if current_wechat_pid() != pid or process_start_ticks(pid) != ticks or result.get('pid') != pid:
            raise RuntimeError('校准期间微信进程变化，未保存。')
        finish_calibration(ROOT, layout, own, peer, result)
        print('当前账号初始化完成。只保存必要账号身份和验证标记，不保存测试好友或测试消息。')
        print('运行 bash reply_all.sh check 检查配置，再运行 bash reply_all.sh。')
        return 0
    except KeyboardInterrupt:
        print('已取消初始化，旧校准保持不变。', file=sys.stderr)
        return 1
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print('初始化未完成：' + message, file=sys.stderr)
        return 1
    finally:
        if receiver:
            receiver.close()
        if worker:
            if worker.poll() is None:
                worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
        if lock:
            lock.close()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
