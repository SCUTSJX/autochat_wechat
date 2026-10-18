"""Persistent, account-scoped sticker cadence and at-most-once task ledger."""
import argparse
import hashlib
from pathlib import Path
import sqlite3
import uuid

from session_context import private_dir


class StickerState:
    def __init__(self, path, account, recover=True):
        path = Path(path).absolute()
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise ValueError('表情状态路径不能包含链接')
        self.account = hashlib.sha256(account.encode('utf-8')).hexdigest()
        if not path.exists():
            from vision_client import private_file
            private_file(path, b'')
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS cadence (
            account TEXT, conversation TEXT, replies INTEGER DEFAULT 0,
            sticker_blocked INTEGER DEFAULT 0, draft_blocked INTEGER DEFAULT 0,
            PRIMARY KEY(account,conversation));
          CREATE TABLE IF NOT EXISTS replies (
            account TEXT, conversation TEXT, message_id TEXT,
            PRIMARY KEY(account,conversation,message_id));
          CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, account TEXT, conversation TEXT, keyword TEXT,
            status TEXT, cleanup TEXT, receipt TEXT);
        ''')
        # Keep uncertain tasks for diagnostics without replaying their Enter.
        if recover:
            self.recover_interrupted()

    def recover_interrupted(self):
        with self.db:
            # Remove legacy sticky pauses. An uncertain result only requires a
            # draft check; a new message remains eligible for a new task.
            self.db.execute('UPDATE cadence SET sticker_blocked=0 WHERE account=?', (self.account,))
            self.db.execute('''UPDATE cadence SET draft_blocked=1
              WHERE account=? AND conversation IN
              (SELECT conversation FROM tasks WHERE account=? AND status='pending')''',
              (self.account, self.account))
            self.db.execute("UPDATE tasks SET status='unknown',cleanup='interrupted' WHERE account=? AND status='pending'",
                            (self.account,))

    def close(self):
        self.db.close()

    def current(self, conversation):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO cadence(account,conversation) VALUES(?,?)',
                            (self.account, conversation))
        return dict(self.db.execute('SELECT * FROM cadence WHERE account=? AND conversation=?',
                                   (self.account, conversation)).fetchone())

    def allowed(self, conversation, minimum):
        row = self.current(conversation)
        return not row['draft_blocked'] and row['replies']+1 >= minimum

    def text_sent(self, conversation, message_id):
        self.current(conversation)
        with self.db:
            cursor = self.db.execute('INSERT OR IGNORE INTO replies VALUES(?,?,?)',
                                     (self.account, conversation, message_id))
            if cursor.rowcount:
                self.db.execute('UPDATE cadence SET replies=replies+1 WHERE account=? AND conversation=?',
                                (self.account, conversation))

    def begin(self, conversation, keyword):
        self.current(conversation)
        task = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',
                            (task, self.account, conversation, keyword, 'pending', 'pending', None))
        return task

    def finish(self, task, status, cleanup, receipt=None, reset_cadence=True, cadence_used=False):
        row = self.db.execute('SELECT * FROM tasks WHERE id=? AND account=?', (task, self.account)).fetchone()
        if row is None or row['status'] != 'pending':
            raise ValueError('表情任务不存在或已完成')
        with self.db:
            self.db.execute('UPDATE tasks SET status=?,cleanup=?,receipt=? WHERE id=?',
                            (status, cleanup, receipt, task))
            draft_blocked = cleanup not in ('cleared', 'empty', 'not_written')
            self.db.execute('''UPDATE cadence SET replies=CASE WHEN ? THEN 0 ELSE replies END,
              sticker_blocked=?,draft_blocked=? WHERE account=? AND conversation=?''',
              ((status == 'confirmed' or cadence_used) and reset_cadence,
               0, int(draft_blocked), self.account, row['conversation']))

    def resume(self, conversation):
        with self.db:
            self.db.execute('UPDATE cadence SET sticker_blocked=0,draft_blocked=0 WHERE account=? AND conversation=?',
                            (self.account, conversation))

    def draft_keyword(self, conversation):
        row = self.db.execute('SELECT keyword FROM tasks WHERE account=? AND conversation=? ORDER BY rowid DESC LIMIT 1',
                              (self.account, conversation)).fetchone()
        return row['keyword'] if row else None

    def draft_recovered(self, conversation, cleanup):
        if cleanup not in ('empty', 'cleared'):
            raise ValueError('只有核验输入框为空或清理成功后才能恢复')
        with self.db:
            self.db.execute('UPDATE cadence SET draft_blocked=0 WHERE account=? AND conversation=?',
                            (self.account, conversation))
            self.db.execute('''UPDATE tasks SET cleanup=? WHERE rowid=(SELECT MAX(rowid) FROM tasks
                              WHERE account=? AND conversation=?)''', (cleanup, self.account, conversation))


def main():
    parser = argparse.ArgumentParser(description='表情状态与恢复；恢复前请检查微信发送结果并清空目标草稿。')
    parser.add_argument('action', choices=('status', 'resume'))
    parser.add_argument('--conversation', help='status 输出中的原始会话 ID')
    args = parser.parse_args()
    from project_setup import ROOT, load_profile
    root = private_dir(ROOT/'temp')
    account = load_profile(ROOT)['calibration_proof']['self_id']
    state = StickerState(root/'sticker-state.sqlite3', account, recover=False)
    lock = None
    try:
        if args.action == 'resume':
            if not args.conversation:
                parser.error('resume 需要 --conversation')
            import fcntl
            lock_path = ROOT/'bot.lock'
            lock = lock_path.open('r' if lock_path.exists() else 'a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error('请先停止自动回复，再检查草稿并恢复会话')
            state.resume(args.conversation)
            print('已解除此会话的表情/草稿暂停；不会重发旧任务。')
        else:
            for row in state.db.execute('SELECT conversation,replies,sticker_blocked,draft_blocked FROM cadence WHERE account=?',
                                        (state.account,)):
                print(dict(row))
            for row in state.db.execute('SELECT conversation,status,cleanup,receipt FROM tasks WHERE account=? ORDER BY rowid DESC LIMIT 20',
                                        (state.account,)):
                print(dict(row))
    finally:
        state.close()
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
