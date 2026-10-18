"""One desktop worker discovers independent windows and serializes all output."""
import json
import select
import sys
import time
from pathlib import Path
from collections import Counter, deque

from atspi_backend import application, resolve, spec, supports
from ui_helpers import rows, unique, visible
from virtual_cursor import XConnection
from virtual_output import matches, send
from group_text import message_text, title_name
from window_stack import WindowStack


def control_nodes(root):
    """Skip list contents: message/contact rows are unnecessary for control discovery."""
    pending = [((), root)]
    while pending:
        path, node = pending.pop()
        yield path, node
        if node.get_role_name() in ('list', 'table', 'tree'):
            continue
        for index in reversed(range(node.get_child_count())):
            child = node.get_child_at_index(index)
            if child is not None:
                pending.append((path + (index,), child))


def prior_context(values, text, limit=12, budget=3000):
    # Stop at the earliest identical message to avoid including queued future messages.
    cutoff = next((index for index,value in enumerate(values) if matches(value, text)), len(values))
    selected = []
    for value in reversed(values[max(0, cutoff-limit):cutoff]):
        value = value.strip()
        if not value:
            continue
        if len(value) > budget:
            break
        selected.append(value)
        budget -= len(value) + 1
    return list(reversed(selected))


def discovery_steps(root, diagnostics=None):
    """Yield between small accessibility operations; never rewalk each frame."""
    nodes = []
    pending = [((), root)]
    while pending:
        path, node = pending.pop()
        role = node.get_role_name()
        yield
        name = node.get_name() or ''
        nodes.append((path, node, role, name))
        yield
        if role in ('list', 'table', 'tree'):
            continue
        count = node.get_child_count()
        yield
        for index in reversed(range(count)):
            child = node.get_child_at_index(index)
            if child is not None:
                pending.append((path + (index,), child))
            yield
    found = {}
    for frame_path, frame, role, name in nodes:
        if role not in ('frame', 'dialog'):
            continue
        shown = visible(frame)
        yield
        if not shown:
            continue
        if diagnostics is not None:
            diagnostics['visible_frames'] += 1
        try:
            inputs, buttons, lists, titles = [], [], [], []
            prefix = tuple(frame_path)
            for path, node, child_role, child_name in nodes:
                if path[:len(prefix)] != prefix:
                    continue
                target = None
                if child_name == 'Enter':
                    if supports(node, 'EditableText') and supports(node, 'Text'):
                        target = inputs
                    yield
                elif child_name == 'Send' and child_role == 'push button':
                    target = buttons
                elif child_name == 'Messages' and child_role == 'list':
                    target = lists
                elif child_role == 'label' and title_name(child_name) == title_name(name):
                    if node.get_child_count() == 0:
                        target = titles
                    yield
                if target is not None:
                    shown = visible(node)
                    yield
                    if shown:
                        target.append((path, node, child_role, child_name))
            editor = unique(inputs, '输入框')
            button = unique(buttons, '发送按钮')
            messages = unique(lists, '消息列表')
            title = unique(titles, '会话标题')
            if not name:
                continue
            def selector(target):
                path, _, role, label = target
                return dict(path=list(path), role=role, name=label)
            route = dict(name=name, frame=dict(path=list(frame_path), role=role, name=name), title=selector(title),
                         input=selector(editor), send=selector(button), messages=selector(messages))
            prefix = list(frame_path)
            if any(route[key]['path'][:len(prefix)] != prefix
                   for key in ('title', 'input', 'send', 'messages')):
                continue
            key = (tuple(frame_path), name)
            found[key] = (route, rows(messages[1]))
            yield
        except RuntimeError as exc:
            if diagnostics is not None:
                reason = str(exc)
                for stage in ('输入框', '发送按钮', '消息列表', '会话标题', '控件路径'):
                    if reason.startswith(stage):
                        diagnostics[stage] = diagnostics.get(stage, 0) + 1
                        break
            continue
    return found


def discover(root, diagnostics=None):
    steps = discovery_steps(root, diagnostics)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


def frame_signature(root):
    """Only top-level identities/titles; do not traverse controls or message rows."""
    result = []
    for index in range(root.get_child_count()):
        node = root.get_child_at_index(index)
        if node is not None:
            result.append((index, node, node.get_role_name(), node.get_name() or ''))
    return tuple(result)


class Windows:
    def __init__(self):
        self.snapshots = {}
        self.changes = {}
        self.routes = {}
        self.owners = {}
        self.connection = None
        self.diagnostics = {}
        self.window_routes = {}
        self.last_discovery = 0
        self.stack = None
        self.topology = None
        self.scan = None
        self.scan_root = None
        self.scan_signature = None
        self.scan_diagnostics = None
        self.discovery_needed = False
        self.last_topology_check = 0
        self.idle_keys = deque()
        self.last_idle_poll = 0
        self.pending_sticker = None
        self.last_sticker_finish = None

    def forget(self, key):
        self.window_routes.pop(key, None)
        self.snapshots.pop(key, None)
        self.changes.pop(key, None)
        peer = self.owners.pop(key, None)
        if peer:
            self.routes.pop(peer, None)

    def update(self, found, replace=False):
        if replace:
            for key in set(self.window_routes) | set(self.snapshots):
                replacement = found.get(key)
                if (replacement is None or (key in self.window_routes and self.window_routes[key] != replacement[0])):
                    self.forget(key)
            self.window_routes = {key:route for key,(route, _) in found.items()}
        now = time.monotonic()
        for key, (route, values) in found.items():
            current = Counter(values)
            old = self.snapshots.get(key)
            changes = self.changes.setdefault(key, deque(maxlen=256))
            # A newly discovered/replaced window starts with historical rows only.
            if old is not None:
                for value, count in (current - old).items():
                    for _ in range(min(count, 256)):
                        changes.append((now, value))
            while changes and changes[0][0] < now - 600:
                changes.popleft()
            self.snapshots[key] = current

    def topology_changed(self, root):
        signature = frame_signature(root)
        if self.topology is None:
            self.topology = signature
            return False
        return self.topology != signature

    def discovery_step(self):
        if self.scan is None:
            root = application()
            self.scan_root = root
            self.scan_signature = frame_signature(root)
            self.scan_diagnostics = {'visible_frames':0}
            self.scan = discovery_steps(root, self.scan_diagnostics)
        try:
            next(self.scan)
        except StopIteration as done:
            # Discard a scan whose window topology changed while it was paused.
            if frame_signature(self.scan_root) == self.scan_signature:
                self.update(done.value, replace=True)
                self.diagnostics = self.scan_diagnostics
                self.topology = self.scan_signature
                self.discovery_needed = False
                self.last_discovery = time.monotonic()
            self.scan = None
        except RuntimeError:
            self.scan = None
            self.discovery_needed = True

    def background_step(self):
        now = time.monotonic()
        if now-self.last_topology_check >= 2:
            root = application()
            self.discovery_needed = self.topology_changed(root) or self.discovery_needed
            self.last_topology_check = now
            return
        if self.discovery_needed:
            # A maximum of 8 cooperative steps, not a whole tree or whole frame.
            for _ in range(8):
                self.discovery_step()
                if not self.discovery_needed:
                    break
            return
        if now-self.last_idle_poll >= 0.5 and self.window_routes:
            if not self.idle_keys:
                self.idle_keys.extend(self.window_routes)
            key = self.idle_keys.popleft()
            if key in self.window_routes:
                self.poll(full=False, key=key)
            self.last_idle_poll = now

    def poll(self, full=True, key=None):
        root = application()
        if full or (key is None and any('messages' not in route for route in self.window_routes.values())):
            self.diagnostics = {'visible_frames':0}
            found = discover(root, self.diagnostics)
            self.update(found, replace=True)
            # Synchronous discovery is limited to startup or unknown/invalid routes.
            self.topology = frame_signature(root)
            self.scan = None
            self.discovery_needed = False
            self.last_discovery = time.monotonic()
        else:
            found = {}
            for current_key, route in list(self.window_routes.items()):
                if key is not None and current_key != key:
                    continue
                try:
                    resolve(root, route['frame'])
                    resolve(root, route['title'])
                    values = rows(resolve(root, route['messages']))
                    found[current_key] = (route, values)
                except RuntimeError:
                    self.forget(current_key)
                    self.discovery_needed = True
                    # Never let a scan paused before this invalidation restore it.
                    self.scan = None
            self.update(found)
        return root, found

    def pointer(self):
        if self.connection is None:
            self.connection = XConnection()
            if not self.connection.execute({'op':'self-test'})['primary_unchanged']:
                raise RuntimeError('主鼠标或焦点变化，未发送。')
            self.stack = WindowStack(self.connection)
        return self.connection

    def expose(self, route, pid):
        self.pointer()
        return self.stack.expose(route['name'], pid)

    def bind(self, event):
        peer = event['conversation_id']
        text = message_text(event)
        deadline = time.monotonic() + (0.25 if event.get('observe_only') else 3)
        known = self.routes.get(peer)
        inspected = set()
        checked_topology = False
        while True:
            if known and known['window_key'] in self.window_routes:
                with self.expose(known, known['pid']):
                    root, found = self.poll(full=False, key=known['window_key'])
            else:
                # Unknown peers may require on-demand discovery, once per request.
                full = False
                if not checked_topology:
                    full = self.discovery_needed or not self.window_routes or self.topology_changed(application())
                    checked_topology = True
                root, found = self.poll(full=full)
            candidates = []
            for key, (route, values) in found.items():
                owner = self.owners.get(key)
                if owner and owner != peer:
                    continue
                for index, (_, value) in enumerate(self.changes[key]):
                    if value in values and matches(value, text):
                        candidates.append((key, route, index))
                        break
            if len(candidates) > 1:
                return {'ok':False, 'reason':'ambiguous', 'diagnostics':self.binding_diagnostics(found, event, text)}
            if len(candidates) == 1:
                key, route, index = candidates[0]
                self.changes[key].rotate(-index)
                self.changes[key].popleft()
                self.changes[key].rotate(index)
                route = dict(route, conversation_id=peer, kind=event['conversation_kind'],
                             verified=True, transport='virtual_cursor', window_key=key,
                             pid=root.get_process_id())
                self.owners[key] = peer
                self.routes[peer] = route
                self.controls(peer)
                return {'name':route['name'], 'kind':route['kind'], 'verified':True,
                        'conversation_id':peer,
                        'context_rows':prior_context(found[key][1], text,
                            min(50, max(0, event.get('context_rows', 12))),
                            min(10000, max(0, event.get('context_chars', 3000))))}
            if known and known['window_key'] not in self.window_routes:
                known = None
            if time.monotonic() >= deadline:
                return {'ok':False, 'reason':'no_match', 'diagnostics':self.binding_diagnostics(found, event, text)}
            # An occluded Qt window may not refresh its accessible rows until uncovered.
            if not known and not event.get('observe_only'):
                for window_key, (candidate, _) in found.items():
                    if window_key in inspected or not candidate.get('frame'):
                        continue
                    inspected.add(window_key)
                    try:
                        with self.expose(candidate, root.get_process_id()):
                            time.sleep(0.05)
                            self.poll(full=False, key=window_key)
                    except RuntimeError:
                        self.diagnostics['layer_refresh_failures'] = self.diagnostics.get('layer_refresh_failures', 0) + 1
            time.sleep(0.1)

    def binding_diagnostics(self, found, event, text):
        return dict(self.diagnostics, available_windows=len(found),
                    new_rows=sum(len(rows) for rows in self.changes.values()),
                    kind=event.get('conversation_kind', 'unknown'),
                    group_prefix_removed=text != event['text'],
                    current_text_matches=sum(matches(value, text)
                        for _, values in found.values() for value in values))

    def observe(self, event):
        peer = event['conversation_id']
        if peer in self.routes:
            self.controls(peer)
            return {'ok':True, 'verified':True}
        return self.bind(dict(event, observe_only=True, await_send=False, context_rows=0))

    def capture_media(self, peer, name, kind, directory):
        from core import ROOT
        from atspi_backend import bounds
        from conversation_capture import capture
        import gi
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk
        target = Path(directory).resolve(strict=True)
        base = (ROOT/'temp').resolve(strict=True)
        if base not in target.parents or Path(directory).is_symlink():
            raise RuntimeError('截图目录不在项目 temp 内。')
        if peer not in self.routes:
            if not name:
                return {'ok':False, 'reason':'conversation_not_bound'}
            root, found = self.poll(full=True)
            candidates = [(key, route) for key,(route, _) in found.items()
                          if title_name(route['name']) == title_name(name)
                          and self.owners.get(key) in (None,peer)]
            if len(candidates) != 1:
                return {'ok':False, 'reason':'explicit_mapping_not_unique'}
            key, route = candidates[0]
            self.owners[key] = peer
            self.routes[peer] = dict(route, conversation_id=peer, kind=kind, verified=True,
                transport='virtual_cursor', window_key=key, pid=root.get_process_id())
        outer = self
        class BoundWindow:
            def geometry(self):
                root, _, _, _ = outer.controls(peer)
                return root.get_process_id(), bounds(resolve(root, outer.routes[peer]['frame']))
        Gdk.init([])
        if Gdk.Display.get_default() is None:
            raise RuntimeError('未连接截图桌面。')
        # Reuse the fast screenshot path; no cursor, raising or cleanup after save.
        return capture(BoundWindow(), target, Gdk.get_default_root_window(), Gdk.pixbuf_get_from_window)

    def controls(self, peer, root=None):
        route = self.routes[peer]
        root = root if root is not None else application()
        if root.get_process_id() != route['pid']:
            raise RuntimeError('微信进程变化，未发送。')
        frame = resolve(root, route['frame'])
        if frame.get_role_name() not in ('frame', 'dialog') or self.owners.get(route['window_key']) != peer:
            raise RuntimeError('会话窗口绑定已失效，未发送。')
        resolve(root, route['title'])
        return (root, resolve(root, route['input'], check_name=False),
                resolve(root, route['send']), resolve(root, route['messages']))

    def output(self, peer, text):
        if peer not in self.routes:
            raise RuntimeError('会话未绑定。')
        self.pointer()
        root = application()
        route = self.routes[peer]
        before = self.connection.status()['primary']
        owned = []
        with self.expose(route, route['pid']):
            try:
                result = send(self.connection, lambda:self.controls(peer, root), text,
                              on_write=lambda: owned.append(True))
            except Exception:
                from sticker_ui import clear_owned_draft
                cleanup = clear_owned_draft(lambda:self.controls(peer), text) if owned else 'not_written'
                print('文字发送未确认；草稿清理：'+cleanup, file=sys.stderr, flush=True)
                raise
        if self.connection.status()['primary'] != before:
            raise RuntimeError('发送期间主鼠标或焦点发生变化，停止且不重复发送。')
        # Refresh only this window, then acknowledge; no all-window scan after sending.
        self.poll(full=False, key=route['window_key'])
        key = route['window_key']
        self.changes[key] = deque((entry for entry in self.changes[key]
                                  if not matches(entry[1], text)), maxlen=256)
        return dict(result, conversation_id=peer)

    def sticker_focus(self, peer):
        """Validate keyboard focus belongs to the exact bound X window."""
        root, field, _, _ = self.controls(peer)
        route = self.routes[peer]
        target = self.stack.target(route['name'], root.get_process_id())
        window = self.connection.status()['primary']['focus_window']
        seen = set()
        for _ in range(32):
            if window == target:
                return
            if window in (0, 1, self.connection.root) or window in seen:
                break
            seen.add(window)
            # Qt completion popups may be separate top-level X windows. Accept
            # only a transient chain back to this chat, never just a shared PID.
            transient = self.stack.property(window, 'WM_TRANSIENT_FOR', 'WINDOW')
            owner = self.stack.property(window, '_NET_WM_PID', 'CARDINAL')
            if transient and owner in (None, [root.get_process_id()]):
                window = transient[0]
            else:
                window, _ = self.stack.tree(window)
        raise RuntimeError('键盘焦点不在原聊天窗口；未发送按键')

    def prepare_sticker(self, peer, keyword, task, timeout=0.3):
        from sticker_library import input_keyword as valid_keyword
        if self.pending_sticker is not None or not valid_keyword(keyword):
            return dict(ok=False, reason='invalid_or_busy', cleanup='not_written')
        if not isinstance(task, str) or len(task) != 32 or any(c not in '0123456789abcdef' for c in task):
            return dict(ok=False, reason='invalid_task', cleanup='not_written')
        self.pointer()
        route = self.routes[peer]
        exposure = self.stack.expose(route['name'], route['pid'], allow_focus=True)
        exposure.__enter__()
        self.pending_sticker = dict(peer=peer, keyword=keyword, task=task,
                                    exposure=exposure, written=False, entered=False,
                                    diagnostics=dict(stage='write', down_sent=False,
                                                     enter_sent=False, wait_seconds=timeout))
        try:
            root, field, _, _ = self.controls(peer)
            text, editable = field.get_text_iface(), field.get_editable_text_iface()
            if text is None or editable is None or text.get_text(0, -1):
                return dict(self.finish_sticker(task), ok=False, reason='existing_draft', cleanup='changed')
            self.connection.focus(self.stack.client(route['name'], route['pid']))
            if not field.get_component_iface().grab_focus():
                raise RuntimeError('输入框无法获得焦点')
            self.sticker_focus(peer)
            self.pending_sticker['written'] = True
            if not editable.set_text_contents(keyword) or text.get_text(0, -1) != keyword:
                raise RuntimeError('表情关键词写入或回读失败')
            # Candidates can be painted without an accessible popup. Follow
            # the configured delay and keyboard sequence unconditionally.
            self.pending_sticker['diagnostics']['stage'] = 'wait'
            time.sleep(timeout)
            _, field, _, _ = self.controls(peer)
            if field.get_text_iface().get_text(0, -1) != keyword:
                raise RuntimeError('等待候选时草稿发生变化')
            self.connection.key('Down', lambda: self.sticker_focus(peer))
            self.pending_sticker['diagnostics']['down_sent'] = True
            self.pending_sticker['diagnostics']['stage'] = 'down'
            time.sleep(0.1)
            return dict(ok=True, task=task, ready=True, diagnostics=dict(self.pending_sticker['diagnostics']))
        except Exception as exc:
            result = self.finish_sticker(task)
            return dict(result, ok=False, reason='prepare_failed', error_type=type(exc).__name__)

    def commit_sticker(self, task):
        pending = self.pending_sticker
        if pending is None or pending['task'] != task or pending['entered']:
            return dict(ok=False, entered=False, reason='invalid_task')
        peer = pending['peer']
        _, field, _, messages = self.controls(peer)
        if field.get_text_iface().get_text(0, -1) != pending['keyword']:
            return dict(ok=False, entered=False, reason='draft_changed')
        self.sticker_focus(peer)
        try:
            pending['keyword_rows_before'] = sum(matches(value, pending['keyword']) for value in rows(messages))
        except Exception:
            pending['keyword_rows_before'] = None  # Optional text fallback readback.
        pending['entered'] = True
        pending['diagnostics']['stage'] = 'enter_attempt'
        self.connection.key('Return', lambda: self.sticker_focus(peer))
        pending['diagnostics']['enter_sent'] = True
        pending['diagnostics']['stage'] = 'enter'
        return dict(ok=True, entered=True, conversation_id=peer, diagnostics=dict(pending['diagnostics']))

    def finish_sticker(self, task):
        from sticker_ui import clear_owned_draft
        pending = self.pending_sticker
        if pending is None or pending['task'] != task:
            if self.last_sticker_finish and self.last_sticker_finish[0] == task:
                return dict(self.last_sticker_finish[1])
            return dict(ok=False, cleanup='invalid_task')
        self.pending_sticker = None
        cleanup = 'not_written'
        keyword_text_seen = False
        try:
            if pending['written']:
                cleanup = clear_owned_draft(lambda: self.controls(pending['peer']), pending['keyword'])
            if pending['entered'] and pending.get('keyword_rows_before') is not None and cleanup in ('empty', 'cleared'):
                _, field, _, messages = self.controls(pending['peer'])
                keyword_text_seen = (field.get_text_iface().get_text(0, -1) == '' and
                    sum(matches(value, pending['keyword']) for value in rows(messages)) > pending['keyword_rows_before'])
        except Exception:
            # A failed optional message read must not revoke a completed clear.
            pass
        result = dict(ok=True, cleanup=cleanup, entered=pending['entered'],
                      keyword_text_seen=keyword_text_seen, diagnostics=pending.get('diagnostics', {}))
        try:
            pending['exposure'].__exit__(None, None, None)
        except Exception as exc:
            result['window_restore_error'] = str(exc)[:300] if isinstance(exc, RuntimeError) else type(exc).__name__
        self.last_sticker_finish = (task, dict(result))
        return result

    def sticker_status(self, task):
        """Read-only text fallback detection; never infer sticker success."""
        pending = self.pending_sticker
        if pending is None or pending['task'] != task:
            return dict(ok=False, keyword_text_seen=False, reason='invalid_task')
        try:
            _, field, _, messages = self.controls(pending['peer'])
            baseline = pending.get('keyword_rows_before')
            seen = (pending['entered'] and baseline is not None and
                    field.get_text_iface().get_text(0, -1) == '' and
                    sum(matches(value, pending['keyword']) for value in rows(messages)) > baseline)
            return dict(ok=True, keyword_text_seen=seen)
        except Exception as exc:
            return dict(ok=False, keyword_text_seen=False, error_type=type(exc).__name__)

    def recover_sticker_draft(self, peer, keyword):
        from sticker_library import input_keyword as valid_keyword
        from sticker_ui import clear_owned_draft
        if keyword is not None and not valid_keyword(keyword):
            return dict(ok=False, cleanup='invalid_keyword')
        cleanup = clear_owned_draft(lambda: self.controls(peer), keyword)
        return dict(ok=cleanup in ('empty', 'cleared'), cleanup=cleanup)

    def verify_initialization(self, peer, name, incoming, outgoing):
        # This is called only by the explicit, interactive init command.
        if (not isinstance(peer, str) or not peer or peer.endswith('@chatroom')
                or not isinstance(name, str) or not name.strip()
                or not isinstance(incoming, str) or not incoming.startswith('wxinit_in_')
                or not isinstance(outgoing, str) or not outgoing.startswith('wxinit_own_')
                or incoming[len('wxinit_in_'):] != outgoing[len('wxinit_own_'):]):
            raise RuntimeError('初始化校验参数无效，未发送。')
        root, found = self.poll(full=True)
        # A duplicate title must never be resolved by a best guess.
        candidates = [(key, route, values) for key, (route, values) in found.items()
                      if title_name(route['name']) == title_name(name)]
        if len(candidates) != 1:
            raise RuntimeError('测试好友窗口名称不唯一或未找到，请打开唯一的聊天独立窗口。')
        key, route, values = candidates[0]
        if not all(any(matches(value, token) for value in values) for token in (incoming, outgoing)):
            raise RuntimeError('测试窗口未同时显示本次两条校验文字，未发送。')
        self.owners[key] = peer
        self.routes[peer] = dict(route, conversation_id=peer, kind='private', verified=True,
                                 transport='virtual_cursor', window_key=key, pid=root.get_process_id())
        result = self.output(peer, '初始化发送校验 ' + incoming[len('wxinit_in_'):])
        if result.get('ui_readback') is not True:
            raise RuntimeError('测试消息未完成原窗口回读，未保存校准。')
        return dict(result, send_verified=True, pid=root.get_process_id())


def main():
    windows = Windows()
    try:
        windows.poll()
        print(json.dumps({'ok':True, 'ready':True}), flush=True)
        waiting_for_send = False
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0.01 if windows.discovery_needed and not waiting_for_send else 0.05)
            if not ready:
                if not waiting_for_send and windows.pending_sticker is None:
                    windows.background_step()
                continue
            line = sys.stdin.readline()
            if not line:
                break
            request = json.loads(line)
            # A paused scan may contain rows older than this foreground request.
            # Restart it later rather than committing stale snapshots after send.
            windows.scan = None
            try:
                if windows.pending_sticker is not None and request['op'] not in ('sticker_commit', 'sticker_status', 'sticker_finish'):
                    raise RuntimeError('表情发送中，桌面操作不能插入')
                if request['op'] == 'sticker_prepare':
                    result = windows.prepare_sticker(request['conversation_id'], request['keyword'],
                        request['task'], min(30, max(0.1, float(request.get('timeout', 0.3)))))
                elif request['op'] == 'sticker_commit':
                    result = windows.commit_sticker(request['task'])
                elif request['op'] == 'sticker_finish':
                    result = windows.finish_sticker(request['task'])
                elif request['op'] == 'sticker_status':
                    result = windows.sticker_status(request['task'])
                elif request['op'] == 'sticker_recover':
                    result = windows.recover_sticker_draft(request['conversation_id'], request.get('keyword'))
                elif request['op'] == 'bind':
                    waiting_for_send = False
                    result = windows.bind(request['event'])
                    waiting_for_send = result.get('verified') is True and request['event'].get('await_send', True)
                elif request['op'] == 'observe':
                    result = windows.observe(request['event'])
                elif request['op'] == 'capture_media':
                    result = windows.capture_media(request['conversation_id'], request.get('mapped_name'),
                        request.get('conversation_kind'), request['directory'])
                elif request['op'] == 'send':
                    waiting_for_send = False
                    result = windows.output(request['conversation_id'], request['text'])
                elif request['op'] == 'init_verify':
                    result = windows.verify_initialization(request['conversation_id'], request['name'],
                        request['incoming'], request['outgoing'])
                elif request['op'] == 'release':
                    waiting_for_send = False
                    result = {'ok':True}
                else:
                    raise RuntimeError('未知操作。')
                response = dict(result)
                response.setdefault('ok', True)
            except RuntimeError as exc:
                response = {'ok':False, 'error':str(exc)}
            except Exception:
                import traceback
                response = {'ok':False, 'error':traceback.format_exc()}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        if windows.pending_sticker is not None:
            try:
                windows.finish_sticker(windows.pending_sticker['task'])
            except Exception:
                print('退出时表情草稿清理未确认。', file=sys.stderr, flush=True)
        if windows.connection:
            windows.connection.close()


if __name__ == '__main__':
    main()
