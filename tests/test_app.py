"""Deterministic integration tests using the REAL recovered engine and a local site."""
import base64
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
import zipfile

TEMP = tempfile.TemporaryDirectory()
os.environ['ZIMUKU_DL_HOME'] = str(Path(TEMP.name) / 'config')
os.environ['DOWNLOAD_DIR'] = str(Path(TEMP.name) / 'downloads')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image
import app
from engine_adapter import Aggregator, ZimukuClient, safe_extract

image_io = BytesIO()
Image.new('RGB', (100, 27), 'green').save(image_io, format='BMP')
CHALLENGE = ('<html>YunsuoAutoJump<input id="intext"><img src="data:image/bmp;base64,' + base64.b64encode(image_io.getvalue()).decode() + '"></html>').encode()
SEARCH = b'''<html><table><tr><td><a href="/detail/123.html" title="Test Movie">Test Movie</a><span class="label">SRT</span></td><td>100</td></tr></table></html>'''


class Site(BaseHTTPRequestHandler):
    challenges = 0
    submissions = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == '/gzip':
            self.send_response(200)
            self.send_header('content-encoding', 'gzip')
            self.end_headers()
            self.wfile.write(gzip.compress(b'<html>compressed subtitle list</html>'))
            return
        if path.path == '/denied':
            self.send_response(402)
            self.end_headers()
            self.wfile.write(b'Access denied')
            return
        answer = parse_qs(path.query).get('security_verify_img', [''])[0]
        if answer:
            Site.submissions.append((answer, self.headers.get('Cookie', '')))
        if answer == '3132333435':
            self.send_response(200)
            self.send_header('Set-Cookie', 'security_session_high_verify=passed; Path=/')
            self.end_headers()
            self.wfile.write(b'<html>verified</html>')
            return
        if 'security_session_high_verify=passed' not in self.headers.get('Cookie', ''):
            Site.challenges += 1
            self.send_response(404)
            self.end_headers()
            self.wfile.write(CHALLENGE)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        if path.path == '/subtitle.srt':
            self.send_header('Content-Disposition', 'attachment; filename="movie.srt"')
        self.end_headers()
        if path.path.startswith('/search'):
            self.wfile.write(SEARCH)
        elif path.path.startswith('/dld/'):
            self.wfile.write(b'<a href="/download/123/subtitle">Download</a>')
        elif path.path.startswith('/download/'):
            self.wfile.write(b'<a href="/subtitle.srt">Download</a>')
        elif path.path == '/subtitle.srt':
            self.wfile.write(b'1\n00:00:01,000 --> 00:00:02,000\nHello\n' * 40)
        else:
            self.wfile.write(b'<html>details</html>')


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.client = TestClient(app.app)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        app.manager.pool.shutdown(wait=True)
        TEMP.cleanup()

    def setUp(self):
        self.client = self.__class__.client

    def builder(self, logger, solver, use_ocr):
        client = ZimukuClient(base_url=self.base, mirrors=(self.base,),
                              logger=logger, manual_solver=solver, use_ocr=False,
                              min_interval=0.01, timeout=2)
        return Aggregator(zimuku_client=client, logger=logger, sources=('zimuku',), min_interval=0.01)

    def wait(self, job_id, statuses):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.client.get('/api/jobs/' + job_id).json()
            if job['status'] in statuses:
                return job
            time.sleep(0.02)
        self.fail(f'Timed out: {job}')

    def test_01_captcha_search_cookie_persistence(self):
        with patch.object(app, 'build_engine', self.builder):
            response = self.client.post('/api/search', json={'keyword':'Test Movie','sources':['zimuku'],'use_ocr':False})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()['id']
            job = self.wait(job_id, {'waiting_captcha','failed'})
            self.assertEqual(job['status'], 'waiting_captcha', job)
            old_id = job['challenge']['id']
            self.assertEqual(self.client.post(f'/api/jobs/{job_id}/captcha',json={'challenge_id':old_id,'answer':'abcde'}).status_code,422)
            self.assertEqual(self.client.post(f'/api/jobs/{job_id}/captcha',json={'challenge_id':'wrong','answer':'12345'}).status_code,409)
            self.client.post(f'/api/jobs/{job_id}/captcha',json={'challenge_id':old_id,'action':'refresh'})
            deadline = time.monotonic()+5
            while time.monotonic()<deadline:
                job = self.client.get('/api/jobs/'+job_id).json()
                if job['challenge'] and job['challenge']['id']!=old_id:
                    break
                time.sleep(.02)
            self.assertNotEqual(job['challenge']['id'],old_id)
            self.client.post(f'/api/jobs/{job_id}/captcha',json={'challenge_id':job['challenge']['id'],'answer':'12345'})
            job = self.wait(job_id, {'done','failed'})
            self.assertEqual(job['status'],'done',job)
            self.assertEqual(job['results'][0]['title'],'Test Movie')
            self.assertEqual(job['results'][0]['source_label'],'字幕库')
            self.assertEqual(Site.submissions[-1][0], '3132333435')
            self.assertIn('srcurl=',Site.submissions[-1][1])
            self.assertIn('security_session_high_verify', (app.CONFIG/'cookies.txt').read_text())
            count = Site.challenges
            again = self.client.post('/api/search',json={'keyword':'Test Movie','sources':['zimuku']}).json()['id']
            self.assertEqual(self.wait(again,{'done','failed'})['status'],'done')
            self.assertEqual(Site.challenges,count,'New client should reuse persisted cookies')
            # Exercise the real download method with the local file endpoint.
            from zimuku_dl.models import DownloadLink
            with patch.object(ZimukuClient,'download_links',return_value=[DownloadLink(label='local',href=self.base+'/subtitle.srt')]):
                response = self.client.post('/api/download',json={'search_id':job_id,'item_id':job['results'][0]['id']})
                downloaded = self.wait(response.json()['id'],{'done','failed'})
                self.assertEqual(downloaded['status'],'done',downloaded)
                file_path = downloaded['files'][0]
                self.assertIn(b'Hello',self.client.get('/api/files/'+file_path).content)
                self.assertTrue(self.client.get('/api/files').json())

    def test_02_cancel_waiting_captcha(self):
        (app.CONFIG/'cookies.txt').unlink(missing_ok=True)
        with patch.object(app,'build_engine',self.builder):
            job_id = self.client.post('/api/search',json={'keyword':'cancel','sources':['zimuku']}).json()['id']
            job = self.wait(job_id,{'waiting_captcha','failed'})
            self.assertEqual(job['status'],'waiting_captcha',job)
            self.client.post(f'/api/jobs/{job_id}/cancel',json={})
            self.assertEqual(self.wait(job_id,{'cancelled','failed'})['status'],'cancelled')

    def test_03_files_auth_origin(self):
        outside = Path(TEMP.name)/'secret.txt'
        outside.write_text('private')
        (app.DOWNLOADS/'symlink.txt').symlink_to(outside)
        self.assertEqual(self.client.get('/api/files/symlink.txt').status_code,404)
        self.assertNotIn('symlink.txt',[f['path'] for f in self.client.get('/api/files').json()])
        self.assertEqual(self.client.get('/api/files/%2e%2e/secret.txt').status_code,404)
        self.assertEqual(self.client.post('/api/search',json={'keyword':'x'},headers={'Origin':'http://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/search',json={'keyword':'   '}).status_code,422)
        with patch.dict(os.environ,{'APP_PASSWORD':'test-password'}):
            self.assertEqual(self.client.get('/api/health').status_code,401)
            self.assertEqual(self.client.get('/api/health',auth=('admin','test-password')).status_code,200)

    def test_04_http_error_is_not_empty_results(self):
        from zimuku_dl.sources.assrt import AssrtSource
        source = AssrtSource(logger=lambda _: None, min_interval=0.01, timeout=2)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 402'):
            source.get_text(self.base + '/denied')

    def test_05_lowercase_gzip_response_header(self):
        from zimuku_dl.sources.assrt import AssrtSource
        source = AssrtSource(logger=lambda _: None, min_interval=0.01, timeout=2)
        self.assertEqual(source.get_text(self.base + '/gzip'), '<html>compressed subtitle list</html>')

    def test_06_signed_download_filename(self):
        from zimuku_dl.sources.assrt import write_file
        path = write_file(Path(TEMP.name)/'signed', 'movie.zip?_=123&token=abc', b'archive')
        self.assertEqual(path.name, 'movie.zip')
        rar = write_file(Path(TEMP.name)/'signed', 'incorrect.zip', b'Rar!\x1a\x07\x00payload')
        self.assertEqual(rar.name, 'incorrect.rar')

    def test_07_subhd_preview_is_identified(self):
        import engine_adapter
        root = Path(TEMP.name)/'preview'
        root.mkdir()
        preview = root/'movie.ass'
        preview.write_text('[00:00:12]\nPreview text\n')
        messages = []
        source = engine_adapter.subhd.SubhdSource(logger=messages.append)
        with patch.object(engine_adapter, '_original_subhd_download', return_value=[preview]):
            output = source.download(None, root)
        self.assertEqual(output[0].name, 'movie.preview.txt')
        self.assertEqual(output[0].read_text(), '[00:00:12]\nPreview text\n')
        self.assertTrue(any('非原始字幕包' in message for message in messages))

    def test_08_zip_limits_and_traversal(self):
        root = Path(TEMP.name)/'archives'
        root.mkdir(exist_ok=True)
        good = root/'good.zip'
        with zipfile.ZipFile(good,'w') as z:
            z.writestr('nested/movie.srt','hello')
        files = safe_extract(good)
        self.assertEqual(len(files),2)
        self.assertEqual(files[1].read_text(),'hello')
        bad = root/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:
            z.writestr('good.srt','should not be written')
            z.writestr('../escaped.srt','bad')
        self.assertEqual(safe_extract(bad),[bad])
        self.assertFalse((root/'escaped.srt').exists())
        self.assertFalse((root/'bad_extracted').exists())
        many = root/'many.zip'
        with zipfile.ZipFile(many,'w') as z:
            for i in range(301):
                z.writestr(f'{i}.srt','x')
        self.assertEqual(safe_extract(many),[many])


if __name__ == '__main__':
    unittest.main(verbosity=2)
