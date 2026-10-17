"""All discoverable conversations, one sender, ten waiting messages globally."""
import argparse
import json
import os
import select
import signal
import subprocess
import sys
import time
import threading
import uuid

from core import ROOT, NativeMessage, reply, reply_destination, settings
from desktop_user import sender_command
from native_receiver import Receiver, current_wechat_pid, validate_layout
from session_context import Store, private_dir, assemble, reply_plan
from media_context import Pipeline, event_for
from context_lifecycle import reset_context
from api_profiles import active_profile
from project_setup import ensure_defaults, load_profile


def request(worker, value, timeout=20):
    worker.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
    worker.stdin.flush()
    if not select.select([worker.stdout], [], [], timeout)[0]:
        raise RuntimeError('窗口进程无响应，停止且不重试。')
    line = worker.stdout.readline()
    if not line:
        raise RuntimeError('窗口进程已退出。')
    return json.loads(line)


def run(receiver, config, worker, stopped, preview=False, store=None, vision_config=None):
    own_store = store is None
    if own_store:
        private_dir(ROOT/'temp')
        store = Store(ROOT/'temp'/'conversation-context')
    desktop_lock = threading.RLock()
    def rpc(value):
        with desktop_lock:
            try:
                return request(worker, value)
            except Exception:
                raise RuntimeError('桌面进程通信失败，停止。') from None
    pipeline = Pipeline(receiver, store, config, rpc, stopped, vision_config, preview)
    def candidates_for(plan, trigger):
        candidates = ([store.get(plan['target'])] if plan['target'] else
                      [item for item in plan['rows'] if item['kind'] in ('image','link','nontext')][-1:])
        if trigger['kind'] == 'link':
            candidates.append(store.get(trigger['seq']))
        return candidates
    last_sent = 0
    waiting = {}
    try:
        pipeline.start()
        while not stopped():
            incoming = pipeline.next_trigger(timeout=0.05)
            if incoming is not None:
                row = store.get(incoming)
                store.update(incoming, reply_status='processing')
                bind_event = dict(event_for(row), context_rows=config.get('visible_context_rows', 12),
                    context_chars=config.get('visible_context_chars', 3000), await_send=False)
                binding = rpc({'op':'bind', 'event':bind_event})
                if not binding.get('ok'):
                    store.update(incoming, reply_status='unbound')
                    print('本条已记录，但未匹配到唯一会话窗口，跳过回复。', flush=True)
                else:
                    event = NativeMessage(row['message_id'], row['conversation'], row['text'], row['direction'],
                        binding['kind'], 'text', False, True, True, binding['name'])
                    destination = reply_destination(config, event)
                    if not destination:
                        store.update(incoming, reply_status='invalid_message')
                    elif preview:
                        store.update(incoming, reply_status='preview')
                        print('消息已记录并绑定；预览不调用模型、不发送。', flush=True)
                    else:
                        visual_profile = active_profile(ROOT, 'vision', require_key=False)
                        vision_timeout = (visual_profile['timeout_seconds'] if visual_profile else
                                          (vision_config or {}).get('vision', {}).get('timeout_seconds',90))
                        waiting[incoming] = dict(binding=binding, destination=destination,
                            started=time.monotonic(), wait_seconds=pipeline.media_wait_seconds(vision_timeout),
                            retried=False, announced=False, timeout_announced=False)
            # Check readiness without blocking other conversations. Keep the first
            # unresolved trigger in each conversation ahead of its later triggers.
            seq = None
            blocked = set()
            for candidate in sorted(waiting):
                state = waiting[candidate]
                row = store.get(candidate)
                if row['conversation'] in blocked:
                    continue
                blocked.add(row['conversation'])
                plan = reply_plan(store, row, config)
                # Capture can report its initial binding failure after bind returns.
                # Retry once, including that late report, without consuming another
                # UI text match or rebinding an already verified conversation.
                if not state['retried']:
                    state['retried'] = bool(pipeline.retry_unbound(candidates_for(plan, row)))
                    plan = reply_plan(store, store.get(candidate), config)
                if plan['wait']:
                    if row['reply_status'] != 'waiting_media':
                        store.update(candidate, reply_status='waiting_media')
                    if not state['announced']:
                        print('本条回复暂缓，等待图片 meaning；其他已就绪会话可继续回复。', flush=True)
                        state['announced'] = True
                    if not state['timeout_announced'] and time.monotonic()-state['started'] >= state['wait_seconds']:
                        print('图片识别超过等待提示时间；继续保留原回复，识别结束后再生成，不发送缺失 meaning 的请求。', flush=True)
                        state['timeout_announced'] = True
                    continue
                seq = candidate
                break
            if seq is None:
                continue
            state = waiting[seq]
            binding, destination = state['binding'], state['destination']
            row = store.get(seq)
            store.update(seq, reply_status='processing')
            # Empty dependency set returns immediately; keep Store's stop/failure
            # check before assembling a request from freshly read, final results.
            store.wait_media([], state['wait_seconds'], lambda:stopped() or pipeline.failure is not None)
            if stopped():
                store.update(seq, reply_status='cancelled')
                break
            if pipeline.failure:
                raise RuntimeError('后台接收失败，停止回复。')
            history, current, plan = assemble(store, store.get(seq), config, binding.get('context_rows', []))
            # No pending relevant image is allowed into a request, even if an
            # asynchronous retry changed status since the readiness check.
            if plan['wait']:
                store.update(seq, reply_status='waiting_media')
                continue
            directory = store.media_directory(destination)
            path = directory/('request-%d-%s.json' % (seq, uuid.uuid4().hex[:8]))
            answer = reply(config, history, current, request_path=path)
            deadline = time.monotonic()+max(0, config['min_reply_interval_seconds']-(time.monotonic()-last_sent))
            while not stopped() and time.monotonic() < deadline:
                time.sleep(min(0.1, max(0, deadline-time.monotonic())))
            if stopped():
                store.update(seq, reply_status='cancelled')
                break
            if pipeline.failure:
                raise RuntimeError('后台接收失败，未发送。')
            response = rpc({'op':'send', 'conversation_id':destination, 'text':answer})
            if (not response.get('ok') or not response.get('ui_readback')
                    or response.get('conversation_id') != destination):
                store.update(seq, reply_status='send_failed')
                raise RuntimeError('发送端未确认原会话发送及回读；停止，不重试。')
            store.assistant(row, answer)
            del waiting[seq]
            last_sent = time.monotonic()
            print('已结合会话记录和 meaning 回复原窗口并完成回读。', flush=True)
    finally:
        pipeline.close()
        with store.lock:
            unfinished = store.db.execute("SELECT seq FROM messages WHERE reply_status IN ('queued','processing','waiting_media')").fetchall()
        for item in unfinished:
            store.update(item['seq'], reply_status='cancelled')
        if own_store:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    receiver = worker = lock = None
    try:
        uid = int(os.environ.get('SUDO_UID', '0'))
        if not sys.platform.startswith('linux') or os.geteuid() != 0 or uid <= 0:
            raise RuntimeError('请在 Ubuntu 普通用户图形终端运行 bash reply_all.sh。')
        import fcntl
        lock = (ROOT/'bot.lock').open('a+')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ensure_defaults(ROOT)
        profile = load_profile(ROOT)
        validate_layout(profile)
        proof = profile.get('calibration_proof', {})
        if not (proof.get('self_id') and proof.get('ui_challenge_match') and proof.get('timestamp_check')):
            raise RuntimeError('缺少已验证的本账号身份，请运行 bash reply_all.sh init。')
        config = settings()
        config.update(max_pending_replies=10)
        vision_path = ROOT/'vision-image-test.json'
        vision_config = json.loads(vision_path.read_text(encoding='utf-8-sig')) if vision_path.exists() else {}
        if not vision_config.get('vision'):
            vision_config['vision'] = dict(config['api'], api_key_env='AI_API_KEY', max_tokens=40000)
        pid = current_wechat_pid()
        command = sender_command(uid)
        command[-1] = str(ROOT/'virtual_cursor.py')
        subprocess.run(command + ['create'], check=True, timeout=30, cwd=str(ROOT))
        command[-1] = str(ROOT/'multi_window_sender.py')
        worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding='utf-8', cwd=str(ROOT))
        if not select.select([worker.stdout], [], [], 20)[0]:
            raise RuntimeError('窗口发现进程未就绪。')
        ready = json.loads(worker.stdout.readline())
        if not ready.get('ready'):
            raise RuntimeError('窗口发现进程启动失败。')
        marker = ROOT/'stop.request'
        marker.unlink(missing_ok=True)
        stop = [False]
        signal.signal(signal.SIGINT, lambda *_:stop.__setitem__(0, True))
        signal.signal(signal.SIGTERM, lambda *_:stop.__setitem__(0, True))
        profile = dict(profile, all_windows=True, reply_queue_limit=config['max_pending_replies'],
                       accept_images=True, accept_nontext=True, record_all_messages=True,
                       group_reply_policy=config.get('group_reply_policy', 'all'),
                       self_mention_names=config.get('self_mention_names', []))
        # Hold bot.lock, and clear history before Receiver or Store can use it.
        cleared = reset_context(ROOT)
        print('上轮会话历史已清空：%d 个文件；本次使用新的文字和图片上下文。' % cleared['files'], flush=True)
        receiver = Receiver(pid, profile)
        print('已监听：先持久记录新消息，图片后台识别；回复等待队列最多 10 条。', flush=True)
        print('回复范围由已打开并唯一绑定的聊天独立窗口决定，不按私聊或群名单筛选。', flush=True)
        if config.get('group_reply_policy') == 'mention_self':
            print('群聊仅回复 @ 你的消息；其他群消息仍作为上下文记录。', flush=True)
        run(receiver, config, worker, lambda:stop[0] or marker.exists(), args.preview, vision_config=vision_config)
        return 0
    except Exception as exc:
        print('未启动或已停止：' + str(exc), file=sys.stderr)
        return 1
    finally:
        if receiver:
            receiver.close()
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
        if lock:
            lock.close()


if __name__ == '__main__':
    sys.exit(main())
