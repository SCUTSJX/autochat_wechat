"""Discard the previous auto-reply session before opening its next Store."""
import errno
from pathlib import Path
import re


DATABASE_FILES = frozenset(('messages.sqlite3', 'messages.sqlite3-wal',
                            'messages.sqlite3-shm', 'messages.sqlite3-journal'))
SESSION_DIRECTORY = re.compile(r'[0-9a-f]{24}\Z')
CAPTURE_FILE = re.compile(r'\d{8}_\d{6}_\d{6}(?:_\d+)?\.png\Z')
REQUEST_FILE = re.compile(r'request-\d+-[0-9a-f]{8}\.json(?:\.[0-9a-f]{32}\.tmp)?\Z')


def checked_path(path, scope, directory=False):
    """Validate the literal absolute destination again before every mutation."""
    if (path != scope and scope not in path.parents) or path.is_symlink() or path.resolve() != path:
        raise RuntimeError('历史清理路径含链接或超出会话目录。')
    if directory:
        if not path.is_dir():
            raise RuntimeError('历史清理位置不是普通目录。')
    elif not path.is_file():
        raise RuntimeError('历史清理目标不是普通文件。')
    return path


def reset_context(project):
    """Clear only generated history; caller must hold bot.lock with no Store open.

    Files are planned and validated first, then individually unlinked. Unknown
    files remain in place; directories are removed only when empty. Runtime
    history is deliberately not archived on each launch.
    """
    project = Path(project).absolute()
    checked_path(project, project, directory=True)
    result = dict(files=0, directories=0)
    temporary = project/'temp'
    if not temporary.exists() and not temporary.is_symlink():
        return result
    checked_path(temporary, project, directory=True)
    root = temporary/'conversation-context'
    if not root.exists() and not root.is_symlink():
        return result
    checked_path(root, project, directory=True)
    files, directories = [], []
    for item in sorted(root.iterdir()):
        if item.name in DATABASE_FILES:
            files.append(checked_path(item, root))
        elif SESSION_DIRECTORY.fullmatch(item.name):
            checked_path(item, root, directory=True)
            directories.append(item)
            for path in sorted(item.iterdir()):
                if (path.name in ('meaning.txt', 'meaning.jsonl')
                        or CAPTURE_FILE.fullmatch(path.name) or REQUEST_FILE.fullmatch(path.name)):
                    files.append(checked_path(path, root))
    # No deletion occurs until every recognized target has passed validation.
    for path in files:
        checked_path(root, project, directory=True)
        checked_path(path, root)
        path.unlink()
        result['files'] += 1
    for path in directories:
        checked_path(root, project, directory=True)
        checked_path(path, root, directory=True)
        try:
            path.rmdir()
            result['directories'] += 1
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
    return result
