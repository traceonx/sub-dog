"""Hard-link subtitles from the library into the media tree, and clean up orphans.

The subtitle library (DOWNLOAD_DIR) is the single place subtitles live. Linking
creates a hard link next to a video, named after it, so media servers pick the
subtitle up. A library file whose link count was observed >= 2 is "tracked";
once its count drops back to 1 (the media folder and its links were deleted)
it is an orphan, and after a grace delay it is deleted from the library too.
Files that were never linked are never touched.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import threading
import time

SUBTITLE_EXTS = {'.srt', '.ass', '.ssa', '.sup', '.vtt', '.sub', '.idx', '.smi'}
VIDEO_EXTS = {'.mkv', '.mp4', '.m4v', '.avi', '.ts', '.m2ts', '.mov', '.wmv', '.rmvb', '.flv', '.webm', '.mpg', '.mpeg'}
LANGS = {'zh-CN': '简体中文', 'zh-TW': '繁体中文', 'zh': '中文', 'en': '英文'}
MIN_DELAY, MAX_DELAY = 5, 7 * 24 * 3600


class LinkError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def guess_lang(name: str) -> str:
    lowered = name.lower()
    if any(k in name for k in ('繁', '正體')) or any(k in lowered for k in ('cht', 'big5', 'zh-tw', 'tc.')):
        return 'zh-TW'
    return 'zh-CN'


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() not in {'0', 'false', 'no', 'off', ''}


def _ident(st) -> list:
    # Inode numbers get reused after deletion, so size + mtime join the identity.
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


class Linker:
    def __init__(self, library: Path, media: Path | None, state_file: Path):
        self.library = library.resolve()
        self.media = media.resolve() if media else None
        self.state_file = state_file
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.settings = {
            'auto_clean': _env_bool('AUTO_CLEAN', True),
            'delay': min(MAX_DELAY, max(MIN_DELAY, int(os.environ.get('CLEAN_DELAY', '30')))),
        }
        # rel library path -> {id: _ident(), links: [media rel paths], orphan_since: float|None}
        self.entries: dict[str, dict] = {}
        self.history: list[dict] = []
        self._load()

    # ---------- persistence ----------
    def _load(self):
        try:
            data = json.loads(self.state_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        settings = data.get('settings') or {}
        if isinstance(settings.get('auto_clean'), bool):
            self.settings['auto_clean'] = settings['auto_clean']
        if isinstance(settings.get('delay'), int):
            self.settings['delay'] = min(MAX_DELAY, max(MIN_DELAY, settings['delay']))
        entries = data.get('entries')
        if isinstance(entries, dict):
            self.entries = {k: v for k, v in entries.items() if isinstance(v, dict) and isinstance(v.get('id'), list)}
        self.history = list(data.get('history') or [])[-50:]

    def _save(self):
        temp = self.state_file.with_suffix('.tmp')
        temp.write_text(json.dumps({'settings': self.settings, 'entries': self.entries,
                                    'history': self.history[-50:]}, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.state_file)

    # ---------- paths ----------
    def library_path(self, rel: str) -> Path:
        path = (self.library / rel).resolve()
        if not path.is_relative_to(self.library) or path == self.library:
            raise LinkError(404, '字幕文件不存在')
        return path

    def media_path(self, rel: str) -> Path:
        if self.media is None:
            raise LinkError(400, '未配置媒体目录（MEDIA_DIR），无法关联到媒体')
        path = (self.media / rel.lstrip('/')).resolve()
        if not path.is_relative_to(self.media):
            raise LinkError(400, '路径超出媒体目录')
        return path

    def browse(self, rel: str = '') -> dict:
        folder = self.media_path(rel)
        if not folder.is_dir():
            raise LinkError(404, '目录不存在')
        dirs, videos = [], []
        try:
            children = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            raise LinkError(403, f'无法读取目录：{exc.strerror or exc}')
        for child in children:
            if child.name.startswith('.') or child.is_symlink():
                continue
            if child.is_dir():
                dirs.append(child.name)
            elif child.suffix.lower() in VIDEO_EXTS:
                videos.append(child.name)
        current = str(folder.relative_to(self.media)).replace(os.sep, '/')
        return {'path': '' if current == '.' else current, 'dirs': dirs[:500], 'videos': videos[:500]}

    # ---------- linking ----------
    def link(self, file_rel: str, video_rel: str, lang: str) -> dict:
        if lang not in LANGS:
            raise LinkError(422, '不支持的语言后缀')
        source = self.library_path(file_rel)
        if not source.is_file() or source.is_symlink():
            raise LinkError(404, '字幕文件不存在')
        if source.suffix.lower() not in SUBTITLE_EXTS:
            raise LinkError(422, '只能关联字幕文件；压缩包请先解压')
        video = self.media_path(video_rel)
        if not video.is_file() or video.suffix.lower() not in VIDEO_EXTS:
            raise LinkError(404, '视频文件不存在')
        target = video.with_name(f'{video.stem}.{lang}{source.suffix.lower()}')
        with self.lock:
            if target.exists():
                if os.path.samefile(target, source):
                    return {'target': self._media_rel(target), 'existing': True}
                raise LinkError(409, f'目标已存在同名字幕：{target.name}')
            try:
                os.link(source, target, follow_symlinks=False)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise LinkError(409, '字幕库与媒体目录不在同一文件系统/挂载点，无法硬链接。'
                                         '请在 Docker 中挂载它们的共同父目录。')
                raise LinkError(403, f'创建硬链接失败：{exc.strerror or exc}')
            ident = _ident(source.stat())
            key = self._key(source)
            entry = self.entries.get(key)
            if not entry or entry['id'][:2] != ident[:2]:
                entry = {'links': []}
                self.entries[key] = entry
            entry['id'] = ident
            rel_target = self._media_rel(target)
            if rel_target not in entry['links']:
                entry['links'].append(rel_target)
            entry['orphan_since'] = None
            self._save()
        return {'target': rel_target, 'existing': False}

    def _key(self, path: Path) -> str:
        return str(path.relative_to(self.library)).replace(os.sep, '/')

    def _media_rel(self, path: Path) -> str:
        return str(path.relative_to(self.media)).replace(os.sep, '/') if self.media else str(path)

    # ---------- orphan cleanup ----------
    def scan(self, now: float | None = None) -> list[str]:
        """One pass: track linked files, start/cancel countdowns, delete expired orphans."""
        now = time.time() if now is None else now
        deleted, changed, seen = [], False, set()
        with self.lock:
            for path in self.library.rglob('*'):
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                key = self._key(path)
                seen.add(key)
                entry = self.entries.get(key)
                ident = _ident(st)
                if st.st_nlink >= 2:
                    if entry is None or entry.get('id', [])[:2] != ident[:2]:
                        self.entries[key] = entry = {'links': [], 'orphan_since': None}
                        changed = True
                    if entry.get('id') != ident:
                        entry['id'] = ident  # still linked: in-place edits are fine
                        changed = True
                    if entry.get('orphan_since') is not None:
                        entry['orphan_since'] = None  # link came back: cancel deletion
                        changed = True
                    continue
                if entry and entry['id'] != ident:
                    # Replaced or modified while unlinked: forget it (never delete on doubt).
                    del self.entries[key]
                    entry, changed = None, True
                if entry is None:
                    continue  # never linked: leave it alone
                if not self.settings['auto_clean']:
                    if entry.get('orphan_since') is not None:
                        entry['orphan_since'] = None
                        changed = True
                    continue
                if entry.get('orphan_since') is None:
                    entry['orphan_since'] = now
                    changed = True
                elif now - entry['orphan_since'] >= self.settings['delay']:
                    if self._delete(path, entry):
                        deleted.append(key)
                        self.history.append({'path': key, 'links': entry.get('links', []), 'deleted_at': now})
                    del self.entries[key]
                    changed = True
            for key in [k for k in self.entries if k not in seen]:
                del self.entries[key]  # removed from the library by hand
                changed = True
            if changed:
                self._save()
        return deleted

    def _delete(self, path: Path, entry: dict) -> bool:
        try:
            st = path.lstat()
            # Re-check right before unlinking: same file, still the only name.
            if _ident(st) != entry['id'] or st.st_nlink != 1:
                return False
            path.unlink()
        except OSError:
            return False
        parent = path.parent
        while parent != self.library and parent.is_relative_to(self.library):
            try:
                parent.rmdir()  # only succeeds when empty
            except OSError:
                break
            parent = parent.parent
        return True

    def keep(self, file_rel: str) -> None:
        """Stop tracking a file so it is never auto-deleted."""
        key = self._key(self.library_path(file_rel))
        with self.lock:
            if self.entries.pop(key, None) is not None:
                self._save()

    def status(self, key: str, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        with self.lock:
            entry = self.entries.get(key)
            if not entry:
                return None
            since = entry.get('orphan_since')
            return {'links': list(entry.get('links', [])),
                    'delete_in': None if since is None else max(0, round(since + self.settings['delay'] - now))}

    def update_settings(self, auto_clean: bool, delay: int) -> dict:
        with self.lock:
            self.settings = {'auto_clean': auto_clean, 'delay': min(MAX_DELAY, max(MIN_DELAY, delay))}
            self._save()
            return self.public_settings()

    def public_settings(self) -> dict:
        return self.settings | {'media_configured': self.media is not None, 'langs': LANGS,
                                'history': self.history[-10:][::-1]}

    # ---------- background thread ----------
    def start(self, interval: float = 5.0):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()

        def loop():
            while not self.stop_event.wait(interval):
                try:
                    self.scan()
                except Exception:  # keep the watcher alive on transient fs errors
                    pass
        self.thread = threading.Thread(target=loop, name='subtitle-linker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
