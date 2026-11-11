"""Browser interaction checks with explicitly mocked network jobs.
The deterministic HTTP/engine tests live separately in tests/test_app.py.
"""
import base64
from io import BytesIO
import json
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

image = BytesIO()
Image.new('RGB', (100, 27), 'white').save(image, format='BMP')
challenge = {'id':'challenge-1', 'image':'data:image/bmp;base64,'+base64.b64encode(image.getvalue()).decode(), 'expires_at':9999999999}
job = {'id':'browser-search', 'kind':'search', 'title':'浏览器测试', 'status':'waiting_captcha',
       'logs':[], 'errors':{}, 'error':'', 'files':[], 'detail':None, 'results':[], 'challenge':challenge}
jobs = []
seen = []


def handler(route):
    request = route.request
    path = request.url.split(':8080', 1)[-1]
    data = request.post_data_json if request.method == 'POST' else None
    seen.append((path, data))
    if path == '/api/settings':
        result = {'keyword':'', 'sources':['zimuku'], 'fmt':'', 'language':'', 'limit':5, 'use_ocr':False}
    elif path == '/api/search':
        assert data['keyword'] == '浏览器测试' and data['limit'] == 5
        jobs.append(job)
        result = {'id':job['id']}
    elif path == '/api/jobs':
        result = list(reversed(jobs))
    elif path.endswith('/captcha'):
        assert data['answer'] == '12345'
        job['challenge'] = None
        job['status'] = 'done'
        job['results'] = [{'id':'item-1','title':'字幕测试条目','source':'zimuku','source_label':'字幕库','fmt':'SRT','language':'简','downloads':10}]
        result = {'ok':True}
    elif path == '/api/detail':
        jobs.append({**job, 'id':'browser-detail', 'kind':'detail', 'results':[], 'detail':{'title':'字幕详情测试','fmt':'SRT','language':'简体中文','rating':'10'}})
        result = {'id':'browser-detail'}
    elif path == '/api/download':
        assert data == {'search_id':'browser-search','item_id':'item-1'}
        jobs.append({**job, 'id':'browser-download', 'kind':'download', 'results':[], 'files':['fixture.srt']})
        result = {'id':'browser-download'}
    elif path == '/api/files':
        result = []
    elif path == '/api/files/fixture.srt':
        route.fulfill(status=200,content_type='application/octet-stream',body='fixture subtitle',headers={'Content-Disposition':'attachment; filename="fixture.srt"'})
        return
    else:
        raise AssertionError(path)
    route.fulfill(status=200,content_type='application/json',body=json.dumps(result))


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
    errors = []
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.route('**/api/**',handler)
    page.goto('http://127.0.0.1:8080/',wait_until='networkidle')
    page.locator('#keyword').fill('浏览器测试')
    page.locator('#search-button').click()
    field = page.get_by_role('textbox',name='验证码',exact=True)
    field.wait_for(timeout=10000)
    field.fill('12345')
    page.wait_for_timeout(1900)  # Cross a poll boundary: the input must retain its value.
    assert field.input_value() == '12345'
    page.get_by_role('button',name='确认',exact=True).click()
    page.get_by_text('字幕测试条目',exact=True).wait_for(timeout=10000)
    page.get_by_role('button',name='查看详情',exact=True).click()
    page.get_by_role('heading',name='字幕详情测试',exact=True).wait_for(timeout=10000)
    page.get_by_role('button',name='关闭 ×',exact=True).click()
    page.get_by_role('button',name='下载 ↓',exact=True).click()
    link = page.get_by_role('link',name='fixture.srt',exact=True)
    link.wait_for(timeout=10000)
    with page.expect_download() as download:
        link.click()
    assert download.value.suggested_filename == 'fixture.srt'
    page.set_viewport_size({'width':390,'height':844})
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    assert not errors, errors
    result = {'checks':['search submission','CAPTCHA input survives polling','CAPTCHA submission','results rendering','detail dialog','download submission','browser file download','mobile overflow'],'js_errors':errors,'network':'mocked jobs, real application static assets'}
    Path('artifacts').mkdir(exist_ok=True)
    Path('artifacts/browser-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
    browser.close()
