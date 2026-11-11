from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import threading
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get('ZIMUKU_DL_HOME', ROOT / 'runtime/config')).resolve()
DOWNLOADS = Path(os.environ.get('DOWNLOAD_DIR', ROOT / 'runtime/downloads')).resolve()
os.environ['ZIMUKU_DL_HOME'] = str(CONFIG)
for directory in (CONFIG, DOWNLOADS):
    directory.mkdir(parents=True, exist_ok=True)

from engine_adapter import build_engine, save_sessions, CaptchaCancelled, fmt_matches, lang_matches

TERMINAL = {'done', 'failed', 'cancelled'}
CAPTCHA_TIMEOUT = 180


class SearchInput(BaseModel):
    keyword: str = Field(min_length=1, max_length=150)
    sources: list[Literal['zimuku', 'subhd', 'assrt']] = Field(
        default_factory=lambda: ['subhd', 'assrt', 'zimuku'], min_length=1, max_length=3)
    limit: int = Field(default=40, ge=1, le=100)
    fmt: str = Field(default='', max_length=12)
    language: str = Field(default='', max_length=20)
    use_ocr: bool = True


class ItemInput(BaseModel):
    search_id: str = Field(max_length=40)
    item_id: str = Field(max_length=40)


class AnswerInput(BaseModel):
    challenge_id: str = Field(max_length=40)
    action: Literal['answer', 'refresh', 'cancel'] = 'answer'
    answer: str = Field(default='', max_length=5)


@dataclass
class Job:
    kind: str
    title: str
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = 'queued'
    use_ocr: bool = True
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    logs: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    items: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    error: str = ''
    detail: dict | None = None
    challenge: dict | None = None
    answer: str | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    answered: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def log(self, message):
        with self.lock:
            if '预览接口保存' in str(message) and not self.warnings:
                self.warnings.append('SubHD 完整下载未成功，已保存预览内容；请核对格式和完整性。')
            self.logs.append(str(message)[-2000:])
            self.logs[:] = self.logs[-150:]

    def snapshot(self):
        with self.lock:
            return {k: getattr(self, k) for k in (
                'id', 'kind', 'title', 'status', 'created', 'logs', 'results',
                'errors', 'warnings', 'files', 'error', 'detail', 'challenge')} | {
                    # Copy mutable fields while holding lock for safe serialization.
                    'logs': list(self.logs), 'results': list(self.results),
                    'errors': dict(self.errors), 'warnings': list(self.warnings), 'files': list(self.files),
                    'challenge': dict(self.challenge) if self.challenge else None}

    def solve(self, image):
        with self.lock:
            if self.cancelled.is_set():
                raise CaptchaCancelled()
            self.answered.clear()
            self.answer = None
            self.challenge = {
                'id': uuid4().hex,
                'image': 'data:image/bmp;base64,' + base64.b64encode(image).decode(),
                'expires_at': time.time() + CAPTCHA_TIMEOUT,
            }
            self.status = 'waiting_captcha'
        signalled = self.answered.wait(CAPTCHA_TIMEOUT)
        with self.lock:
            self.challenge = None
            self.status = 'running'
            if not signalled:
                self.cancelled.set()
                self.log('验证码等待超时，任务已取消。')
            if self.cancelled.is_set():
                raise CaptchaCancelled()
            return self.answer  # Empty string requests a new challenge in original engine.


class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        # Serialize engine sessions and Cookie writes across all jobs.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='subtitle')

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(404, '任务已过期或不存在，请重新搜索')
            return job

    def submit(self, kind, title, work, use_ocr=True):
        with self.lock:
            if sum(j.status not in TERMINAL for j in self.jobs.values()) >= 8:
                raise HTTPException(429, '任务队列已满，请稍后重试')
            for key in list(self.jobs):
                if len(self.jobs) < 40:
                    break
                if self.jobs[key].status in TERMINAL:
                    del self.jobs[key]
            job = Job(kind=kind, title=title, use_ocr=use_ocr)
            self.jobs[job.id] = job
            self.pool.submit(self.run, job, work, use_ocr)
            return {'id': job.id}

    @staticmethod
    def run(job, work, use_ocr):
        engine = None
        try:
            if job.cancelled.is_set():
                return
            job.status = 'running'
            engine = build_engine(job.log, job.solve, use_ocr)
            work(job, engine)
            job.status = 'done'
        except Exception as exc:
            job.error = str(exc)[:3000] or type(exc).__name__
            job.log(job.error)
            job.status = 'failed'
        finally:
            if engine is not None:
                try:
                    save_sessions(engine)
                except Exception as exc:
                    job.log(f'会话保存失败：{exc}')
            with job.lock:
                job.challenge = None
                if job.cancelled.is_set():
                    job.status = 'cancelled'


manager = Manager()
app = FastAPI(title='字幕工作台', docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def access_control(request: Request, call_next):
    password = os.environ.get('APP_PASSWORD', '')
    if password:
        expected = base64.b64encode(('admin:' + password).encode()).decode()
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Basic ' + expected):
            return Response(status_code=401, headers={'WWW-Authenticate': 'Basic realm="subtitle", charset="UTF-8"'})
    origin = request.headers.get('origin')
    if request.method in {'POST', 'PUT', 'DELETE', 'PATCH'} and origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': '不允许跨站操作'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
    return response


@app.get('/')
def index():
    return FileResponse(ROOT / 'static/index.html')


@app.get('/static/{name}')
def static_file(name: Literal['app.js', 'style.css']):
    return FileResponse(ROOT / 'static' / name)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'engine': 'recovered-cpython-3.12', 'version': '0.1.0'}


@app.get('/api/settings')
def settings():
    try:
        return SearchInput.model_validate_json((CONFIG / 'web.json').read_text()).model_dump()
    except (OSError, ValueError):
        return SearchInput(keyword='电影名称').model_dump() | {'keyword': ''}


@app.post('/api/search', status_code=202)
def search(data: SearchInput):
    data.keyword = data.keyword.strip()
    if not data.keyword:
        raise HTTPException(422, '请输入搜索关键词')

    def work(job, engine):
        items, errors = engine.search_with_report(data.keyword, limit=data.limit,
                                                  sources=list(dict.fromkeys(data.sources)))
        with job.lock:
            job.errors = errors
            if job.cancelled.is_set():
                return
            for item in items:
                if data.fmt and not fmt_matches(item.fmt, data.fmt):
                    continue
                if data.language and not lang_matches(item.language, item.title, data.language):
                    continue
                item_id = uuid4().hex
                job.items[item_id] = item
                job.results.append(item.as_dict() | {'id': item_id, 'source_label': item.source_label() if callable(item.source_label) else item.source_label})
            if errors and not items:
                raise RuntimeError('所选字幕源均未返回结果，请查看各来源错误。')
            job.log(f'搜索完成，找到 {len(job.results)} 条结果。')
    result = manager.submit('search', data.keyword, work, data.use_ocr)
    # Only one API write at a time; atomic replace avoids partial settings.
    with manager.lock:
        temp = CONFIG / 'web.json.tmp'
        temp.write_text(data.model_dump_json(), encoding='utf-8')
        temp.replace(CONFIG / 'web.json')
    return result


def selected_item(data):
    search_job = manager.get(data.search_id)
    with search_job.lock:
        if search_job.kind != 'search' or search_job.status != 'done' or data.item_id not in search_job.items:
            raise HTTPException(404, '搜索结果已失效，请重新搜索')
        return search_job.items[data.item_id]


@app.post('/api/download', status_code=202)
def download(data: ItemInput):
    item = selected_item(data)

    def work(job, engine):
        # Per-task directory avoids overwrites across downloads.
        out = DOWNLOADS / job.id
        files = engine.download(item, out)
        with job.lock:
            job.files = [str(Path(p).resolve().relative_to(DOWNLOADS)) for p in files
                         if Path(p).is_file() and Path(p).resolve().is_relative_to(DOWNLOADS)]
            if not job.files:
                raise RuntimeError('下载未生成文件')
    return manager.submit('download', item.title, work, manager.get(data.search_id).use_ocr)


@app.post('/api/detail', status_code=202)
def detail(data: ItemInput):
    item = selected_item(data)

    def work(job, engine):
        from dataclasses import asdict
        value = asdict(engine.detail(item))
        with job.lock:
            job.detail = value
    return manager.submit('detail', item.title, work, manager.get(data.search_id).use_ocr)


@app.get('/api/jobs')
def jobs():
    with manager.lock:
        return [job.snapshot() for job in reversed(list(manager.jobs.values()))]


@app.get('/api/jobs/{job_id}')
def job_state(job_id: str):
    return manager.get(job_id).snapshot()


@app.post('/api/jobs/{job_id}/captcha')
def answer(job_id: str, data: AnswerInput):
    job = manager.get(job_id)
    with job.lock:
        if not job.challenge or job.challenge['id'] != data.challenge_id or job.answered.is_set():
            raise HTTPException(409, '验证码已失效，请等待刷新')
        if data.action == 'answer' and (len(data.answer) != 5 or not data.answer.isascii() or not data.answer.isdigit()):
            raise HTTPException(422, '请输入 5 位数字')
        if data.action == 'cancel':
            job.cancelled.set()
        job.answer = data.answer if data.action == 'answer' else ''
        job.challenge = None
        job.answered.set()
    return {'ok': True}


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    job = manager.get(job_id)
    with job.lock:
        if job.status in TERMINAL:
            return {'ok': True}
        job.cancelled.set()
        job.challenge = None
        job.answered.set()
        job.log('已请求取消；正在进行的网络请求会在返回或超时后结束。')
    return {'ok': True}


@app.get('/api/files')
def files():
    result = []
    for path in DOWNLOADS.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(DOWNLOADS):
            result.append({'path': str(path.relative_to(DOWNLOADS)), 'name': path.name,
                           'size': path.stat().st_size, 'modified': path.stat().st_mtime})
        if len(result) >= 1000:
            break
    return sorted(result, key=lambda f: f['modified'], reverse=True)


@app.get('/api/files/{file_path:path}')
def get_file(file_path: str):
    path = (DOWNLOADS / file_path).resolve()
    if not path.is_relative_to(DOWNLOADS) or not path.is_file():
        raise HTTPException(404, '文件不存在')
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')
