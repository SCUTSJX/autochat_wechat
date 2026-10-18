"""Independent outgoing-event receipt; never waits for desktop/ingestion locks."""
from collections import deque
import threading
import time
from group_text import message_text


class SentEvents:
    def __init__(self, limit=1024):
        self.condition = threading.Condition()
        self.events = deque(maxlen=limit)
        self.sequence = 0
        self.seen = set()
        self.ids = deque()
        self.limit = limit

    def record(self, payload):
        if payload.get('direction') != 'outgoing':
            return
        identity = (payload.get('conversation_id'), payload.get('message_id'))
        if not all(identity) or identity[1] == '0':
            return
        with self.condition:
            if identity in self.seen:
                return
            self.seen.add(identity)
            self.ids.append(identity)
            if len(self.ids) > self.limit * 4:
                self.seen.discard(self.ids.popleft())
            self.sequence += 1
            self.events.append((self.sequence, dict(payload)))
            self.condition.notify_all()

    def mark(self):
        with self.condition:
            return self.sequence

    def wait(self, conversation, after, timeout, stopped=lambda: False, keyword=None):
        deadline = time.monotonic() + timeout
        unexpected = None
        with self.condition:
            while not stopped():
                for sequence, event in self.events:
                    if sequence > after and event['conversation_id'] == conversation:
                        if event.get('message_kind') in ('image', 'nontext'):
                            return dict(status='confirmed', message_id=event['message_id'])
                        if event.get('message_kind') == 'text':
                            if keyword is None or message_text(event).strip() == keyword:
                                unexpected = dict(status='unexpected_text', message_id=event['message_id'])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, 0.1))
        return unexpected or dict(status='unknown')
