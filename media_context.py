"""Independent ingestion, screenshot and vision queues for the reply timeline."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import queue
import re
import threading
import time

from session_context import BEIJING, reply_trigger


SCREENSHOT_NAME = re.compile(r'\d{8}_\d{6}_\d{6}(?:_\d+)?\.png\Z')


def remove_screenshot(root, conversation, relative):
    """Delete one recorded capture only, never traverse links or scan temp."""
    root = Path(root).absolute()
    value = Path(relative)
    expected = hashlib.sha256(conversation.encode('utf-8')).hexdigest()[:24]
    if (value.is_absolute() or len(value.parts) != 2 or value.parts[0] != expected
            or not SCREENSHOT_NAME.fullmatch(value.name)):
        raise ValueError('不是本程序登记的会话截图路径。')
    path = root/value
    if (root.resolve() != root or root.is_symlink() or path.parent.is_symlink()
            or path.is_symlink() or path.parent.resolve() != root/expected):
        raise ValueError('截图清理不接受链接或目录外路径。')
    try:
        if not path.is_file() and path.exists():
            raise ValueError('截图位置不是普通文件。')
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def event_for(row):
    return dict(conversation_id=row['conversation'], message_id=row['message_id'], text=row['text'],
                conversation_kind=row['conversation_kind'], direction=row['direction'],
                message_kind='text', created_at=row['created_at'], await_send=False)


class Pipeline:
    def __init__(self, receiver, store, config, rpc, stopped, vision_config=None, preview=False, analyse=None):
        self.receiver, self.store, self.config, self.rpc = receiver, store, config, rpc
        self.stopped, self.preview = stopped, preview
        self.vision_config = vision_config or {}
        self.analyse = analyse
        self.vision_concurrency = config.get('vision_concurrency', 4)
        if type(self.vision_concurrency) is not int or not 1 <= self.vision_concurrency <= 10:
            raise ValueError('vision_concurrency 必须为 1 到 10 的整数。')
        self.triggers = queue.Queue(maxsize=10)
        self.ui = queue.Queue(maxsize=10)
        self.vision = queue.Queue(maxsize=10)
        self.finish = threading.Event()
        self.failure = None
        self.threads = []
        self.observing = set()
        self.observer_lock = threading.RLock()
        # Includes captures whose DB/log update failed before recognition queued.
        self.screenshots = {}

    def start(self):
        if not self.preview:
            self.cleanup_screenshots()
        targets = [self.ingest, self.capture] + [self.recognize] * self.vision_concurrency
        for target in targets:
            thread = threading.Thread(target=target, daemon=False)
            self.threads.append(thread)
            thread.start()

    def ingest_one(self, payload):
        row, fresh = self.store.record(payload)
        if not fresh:
            return row
        trigger = reply_trigger(row, self.config)
        if not trigger and row['kind'] in ('text', 'link'):
            with self.observer_lock:
                if row['conversation'] not in self.observing:
                    try:
                        self.ui.put_nowait(('observe', row['seq']))
                        self.observing.add(row['conversation'])
                    except queue.Full:
                        pass  # Optional discovery is skipped; message is persisted.
        if row['media_status'] == 'queued':
            if self.preview:
                self.store.update(row['seq'], media_status='preview', error='preview_no_api')
            else:
                try:
                    self.ui.put_nowait(('capture', row['seq']))
                except queue.Full:
                    self.store.update(row['seq'], media_status='failed', error='capture_queue_full')
        if trigger:
            # Deferred media replies still occupy a slot after leaving Queue.
            with self.store.lock:
                count = self.store.db.execute("SELECT COUNT(*) FROM messages WHERE reply_status IN ('queued','processing','waiting_media')").fetchone()[0]
                try:
                    if count >= 10:
                        raise queue.Full
                    self.store.update(row['seq'], reply_status='queued')
                    self.triggers.put_nowait(row['seq'])
                except queue.Full:
                    self.store.update(row['seq'], reply_status='queue_full')
                    print('回复等待队列已满；本条已记录，跳过回复。', flush=True)
        return row

    def ingest(self):
        try:
            while not self.finish.is_set() and not self.stopped():
                payload = self.receiver.receive(0.1)
                if payload:
                    self.ingest_one(payload)
        except Exception as exc:
            self.failure = type(exc).__name__
            self.finish.set()

    def capture(self):
        while not self.finish.is_set():
            try:
                operation, seq = self.ui.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self.stopped():
                    continue
                row = self.store.get(seq)
                if operation == 'observe':
                    self.rpc({'op':'observe', 'event':event_for(row)})
                    continue
                self.store.update(seq, media_status='capturing')
                mapping = self.vision_config.get('conversations', {}).get(row['conversation'], {})
                directory = self.store.media_directory(row['conversation'])
                response = self.rpc(dict(op='capture_media', conversation_id=row['conversation'],
                    mapped_name=mapping.get('name'), conversation_kind=row['conversation_kind'], directory=str(directory)))
                if not response.get('ok'):
                    self.store.update(seq, media_status='failed', error=response.get('reason', 'capture_failed'))
                    print('图片截图未完成；失败状态已记录。', flush=True)
                    continue
                name = response['screenshot']
                if Path(name).name != name:
                    raise RuntimeError('截图路径无效。')
                screenshot = directory/name
                relative = str(screenshot.relative_to(self.store.root))
                self.screenshots[(row['conversation'], relative)] = seq
                self.store.update(seq, media_status='pending', screenshot=relative)
                record = dict(session_key=directory.name,
                    triggered_at=datetime.fromtimestamp(row['created_at'],BEIJING).isoformat(),
                    captured_at=response['captured_at'], message_kind=row['kind'], direction=row['direction'],
                    ui_changed=response.get('ui_changed'), trigger_text=row['text'],
                    deferred_capture=row.get('error') == 'late_capture_after_binding',
                    preceding_text=[dict(text=item['text'][:1200], direction=item['direction']) for item in
                        self.store.recent(row['conversation'], row['seq'], 8) if item['kind']=='text'])
                if record['deferred_capture']:
                    record['trigger_text'] += '\n程序备注：此截图是在后续提及机器人并完成窗口绑定后补拍，请按触发时间定位；无法对应时返回不确定。'
                self.meaning_log(directory, dict(record, screenshot=name, status='pending'))
                try:
                    self.vision.put_nowait((seq, screenshot, record))
                except queue.Full:
                    self.store.update(seq, media_status='failed', error='vision_queue_full')
            except Exception as exc:
                if operation == 'capture':
                    self.store.update(seq, media_status='failed', error=type(exc).__name__)
                # A broken desktop protocol cannot safely be used for later sends.
                if isinstance(exc, (BrokenPipeError, EOFError)) or (type(exc) is RuntimeError and '进程' in str(exc)):
                    self.failure = type(exc).__name__
                    self.finish.set()
            finally:
                if operation == 'observe':
                    with self.observer_lock:
                        self.observing.discard(self.store.get(seq)['conversation'])
                self.ui.task_done()

    def meaning_log(self, directory, record):
        from vision_client import private_file
        # Creation must share the append lock: private_file uses O_EXCL.
        # Multiple results for the same conversation may finish together.
        with self.store.lock:
            path = directory/'meaning.jsonl'
            if not path.exists():
                private_file(path, b'')
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False)+'\n')
                stream.flush()
            if record['status'] != 'pending':
                path = directory/'meaning.txt'
                if not path.exists():
                    private_file(path, b'')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(record['captured_at']+'\t'+(record.get('analysis',{}).get('content') or '['+record['status']+']').replace('\n',' ')+'\n')

    def media_wait_seconds(self, vision_timeout):
        if 'media_wait_seconds' in self.config:
            return self.config['media_wait_seconds']
        # Include queued captures and requests in flight, not just Queue.qsize().
        pending = len(self.pending_rows())
        batches = max(1, (pending + self.vision_concurrency - 1) // self.vision_concurrency)
        return batches * (vision_timeout + 10)

    def recognize(self):
        from vision_analysis import analyse
        while not self.finish.is_set():
            try:
                seq, screenshot, record = self.vision.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self.stopped():
                    continue
                analysis = (self.analyse or analyse)(self.vision_config.get('vision', {}), screenshot, record)
                status = 'success' if analysis['target_found'] else 'uncertain'
                self.store.update(seq, media_status=status, meaning_json=json.dumps(analysis, ensure_ascii=False))
                self.meaning_log(screenshot.parent, dict(record, screenshot=screenshot.name, status=status, analysis=analysis))
                print('图片 meaning 已更新：'+status, flush=True)
            except Exception as exc:
                try:
                    self.store.update(seq, media_status='failed', error=type(exc).__name__)
                    failure = dict(record, screenshot=screenshot.name, status='failed', error_type=type(exc).__name__)
                    if hasattr(exc, 'response') and isinstance(exc.response, str):
                        failure['model_response'] = exc.response[:24000]
                    self.meaning_log(screenshot.parent, failure)
                except Exception as write_error:
                    self.failure = type(write_error).__name__
                    self.finish.set()
            finally:
                self.vision.task_done()

    def next_trigger(self, timeout=0.2):
        if self.failure:
            raise RuntimeError('上下文接收或桌面进程异常：'+self.failure)
        try:
            return self.triggers.get(timeout=timeout)
        except queue.Empty:
            return None

    def retry_unbound(self, rows):
        queued = 0
        for row in rows:
            row = self.store.get(row['seq'])
            if row['media_status'] == 'failed' and row['error'] in ('conversation_not_bound','explicit_mapping_not_unique'):
                try:
                    self.store.update(row['seq'], media_status='queued', error='late_capture_after_binding')
                    self.ui.put_nowait(('capture', row['seq']))
                    queued += 1
                except queue.Full:
                    self.store.update(row['seq'], media_status='failed', error='capture_queue_full')
        return queued

    def close(self):
        self.finish.set()
        for thread in self.threads:
            if thread.ident is not None:
                thread.join()
        try:
            for row in self.pending_rows():
                self.store.update(row['seq'], media_status='failed', error='stopped_before_completion')
        finally:
            if not self.preview:
                self.cleanup_screenshots()

    def cleanup_screenshots(self):
        """Called before workers start or after all workers have joined."""
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError('后台线程仍在使用截图，不能清理。')
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT seq,conversation,screenshot FROM messages WHERE screenshot IS NOT NULL AND screenshot != ''").fetchall()
        candidates = {(row['conversation'], row['screenshot']): row['seq'] for row in rows}
        candidates.update(self.screenshots)
        result = dict(deleted=0, missing=0, failed=0)
        for key, seq in candidates.items():
            conversation, relative = key
            try:
                removed = remove_screenshot(self.store.root, conversation, relative)
                result['deleted' if removed else 'missing'] += 1
                # Meaning and media/reply statuses remain available across restarts.
                self.store.update(seq, screenshot='')
                self.screenshots.pop(key, None)
            except (OSError, ValueError):
                result['failed'] += 1
        if result['deleted'] or result['failed']:
            print('截图自动清理：已删除 %d 张，未完成 %d 张。' %
                  (result['deleted'], result['failed']), flush=True)
        return result

    def pending_rows(self):
        with self.store.lock:
            return [self.store.decode(row) for row in self.store.db.execute(
                "SELECT * FROM messages WHERE media_status IN ('queued','capturing','pending')").fetchall()]
