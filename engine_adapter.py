"""Compatibility boundary for the recovered CPython 3.12 engine."""
from pathlib import Path
import shutil
import gzip
import re
from io import BytesIO
import stat
import sys
import zipfile
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).parent / 'engine'))
from zimuku_dl.client import ZimukuClient
from zimuku_dl.aggregate import Aggregator
from zimuku_dl.captcha import CaptchaCancelled
from zimuku_dl.session import ZimukuSession
from zimuku_dl.filters import fmt_matches, lang_matches
from zimuku_dl.sources import base, assrt, subhd

SUBTITLE_EXTS = {'.srt', '.ass', '.ssa', '.sup', '.sub', '.idx', '.vtt', '.txt'}


def safe_extract(path, logger=None):
    """Extract bounded regular ZIP subtitles; retain RAR/7z for manual download."""
    path = Path(path)
    log = logger or (lambda _: None)
    if path.suffix.lower() != '.zip':
        if path.suffix.lower() in {'.rar', '.7z'}:
            log('RAR/7z 保留原压缩包，可下载到本机解压。')
        return [path]
    target = path.with_name(path.stem + '_extracted')
    output = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 300 or sum(m.file_size for m in members) > 100 * 1024 * 1024:
                raise ValueError('压缩包超过展开限制')
            # Validate every entry before writing any file.
            for member in members:
                name = member.filename.replace('\\', '/')
                dest = (target / name).resolve()
                mode = member.external_attr >> 16
                if not dest.is_relative_to(target.resolve()) or name.startswith('/') or ':' in name:
                    raise ValueError('压缩包包含越界路径')
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError('压缩包包含特殊文件')
                if member.flag_bits & 1:
                    raise ValueError('不支持加密压缩包')
            for member in members:
                if member.is_dir() or Path(member.filename).suffix.lower() not in SUBTITLE_EXTS:
                    continue
                dest = target / member.filename.replace('\\', '/')
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, dest.open('xb') as sink:
                    shutil.copyfileobj(source, sink, length=65536)
                output.append(dest)
        if output:
            log(f'已安全解压 {len(output)} 个字幕文件')
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        log(f'保留原压缩包：{exc}')
    return [path] + output


# Cookie domains must omit the port, including local integration test sites.
ZimukuSession.host = property(lambda self: urlsplit(self.base_url).hostname or '')

# Preserve the engine's retries, but never parse an HTTP error page as zero results.
_original_fetch = base.SubtitleSource.fetch


def checked_fetch(self, *args, **kwargs):
    body, status, headers, final = _original_fetch(self, *args, **kwargs)
    headers = dict(headers)
    for key, value in list(headers.items()):
        headers.setdefault(key.title(), value)
    if status >= 400:
        raise RuntimeError(f'{self.label} 访问失败：HTTP {status}（{urlsplit(final).hostname}）')
    # HTTP field names are case insensitive. The original dict lookup was not.
    encoding = next((str(v).lower() for k, v in headers.items() if k.lower() == 'content-encoding'), '')
    if 'gzip' in encoding and body.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=BytesIO(body)) as stream:
            body = stream.read(64 * 1024 * 1024 + 1)
        if len(body) > 64 * 1024 * 1024:
            raise RuntimeError('HTTP 解压内容超过 64 MiB 限制')
    return body, status, headers, final


base.SubtitleSource.fetch = checked_fetch

_original_write = base.write_file


def checked_write(out_dir, name, data):
    # The old URL fallback retained signed query parameters in the filename.
    name = re.sub(r'(\.(?:zip|rar|7z|srt|ass|ssa|sup|sub|idx|vtt))(?:\?|#).*$',
                  lambda match: match.group(1), unquote(str(name)), flags=re.I)
    # Some sites send a RAR payload with a .zip filename. Trust archive signatures.
    for magic, suffix in [(b'Rar!\x1a\x07', '.rar'), (b'PK\x03\x04', '.zip'), (b'7z\xbc\xaf\x27\x1c', '.7z')]:
        if data.startswith(magic):
            name = str(Path(name).with_suffix(suffix))
            break
    return _original_write(out_dir, name, data)


base.write_file = checked_write
assrt.write_file = checked_write
subhd.write_file = checked_write

_original_subhd_download = subhd.SubhdSource.download


def subhd_download(self, item, out_dir):
    paths = _original_subhd_download(self, item, out_dir)
    result = []
    for path in paths:
        path = Path(path)
        with path.open('rb') as stream:
            prefix = stream.read(100)
        if re.match(rb'^\[\d{2}:\d{2}:\d{2}\]', prefix):
            renamed = path.with_suffix('.preview.txt')
            path.rename(renamed)
            path = renamed
            self.log('[SubHD] 此文件为带时间标记的预览文本，非原始字幕包：' + path.name)
        result.append(path)
    return result


subhd.SubhdSource.download = subhd_download

# Replace both the original implementation and its imported aliases.
base.extract_archive = safe_extract
assrt.extract_archive = safe_extract
subhd.extract_archive = safe_extract
ZimukuClient._extract_if_archive = lambda self, path: safe_extract(path, self.log)


def build_engine(logger, manual_solver, use_ocr=True):
    client = ZimukuClient(logger=logger, manual_solver=manual_solver,
                          use_ocr=use_ocr, timeout=15, min_interval=1.2)
    return Aggregator(zimuku_client=client, logger=logger, min_interval=1.2, timeout=15)


def save_sessions(engine):
    engine.zimuku_client.session.save_cookies()
    for source in engine._sources.values():
        source.save()
