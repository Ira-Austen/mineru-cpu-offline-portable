"""兼容页服务的端到端测试：用假上游验证上传、转发、结果与产物流转。"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
spec = importlib.util.spec_from_file_location('compat_server', ROOT / 'app' / 'compat_server.py')
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)

MARKDOWN = '# 标题\n\n![fig](images/figure.jpg)\n\nCPU OCR 1234.56\n'


def build_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('images/figure.jpg', b'\xff\xd8\xff\xe0fake-jpeg')
        archive.writestr('a.md', MARKDOWN)
    return buffer.getvalue()


class FakeUpstream(BaseHTTPRequestHandler):
    """最小 V1 上游：档位、任务、结果与文件内容。"""

    seen = []
    markdown = MARKDOWN
    zipped = build_zip()

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, content_type='application/octet-stream'):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route == '/v1/tiers':
            self._json({'data': [{'id': 'standard', 'description': 'high'},
                                 {'id': 'advanced', 'description': 'xhigh'}]})
            return
        if route == '/v1/tasks/task-1':
            self._json({'task_id': 'task-1', 'status': 'completed', 'files': [
                {'name': 'a.pdf', 'status': 'completed',
                 'output_files': {'markdown': {'file_id': 'md-1'},
                                  'zip': {'file_id': 'zip-1'}}}]})
            return
        if route == '/v1/tasks/task-1/result':
            self._json({'task_id': 'task-1', 'status': 'completed', 'files': [
                {'name': 'a.pdf', 'status': 'completed',
                 'content': {'markdown': self.markdown, 'middle_json': {'pages': []}},
                 'output_files': {'markdown': {'file_id': 'md-1'},
                                  'zip': {'file_id': 'zip-1'}}}]})
            return
        if route == '/v1/files/md-1/content':
            self._bytes(self.markdown.encode('utf-8'), 'text/markdown')
            return
        if route == '/v1/files/zip-1/content':
            self._bytes(self.zipped, 'application/zip')
            return
        self._json({'error': {'message': 'not found'}}, status=404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/v1/tasks':
            self._json({'error': {'message': 'not found'}}, status=404)
            return
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        FakeUpstream.seen.append({
            'query': urllib.parse.parse_qs(parsed.query),
            'content_type': self.headers.get('Content-Type'),
            'authorization': self.headers.get('Authorization'),
            'body': body,
        })
        self._json({'task_id': 'task-1', 'status': 'queued'}, status=202)

    def do_DELETE(self):
        self._json({'task_id': 'task-1', 'status': 'canceled'})


class CompatServerTests(unittest.TestCase):
    def setUp(self):
        FakeUpstream.seen = []
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeUpstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.upstream.shutdown)
        self.addCleanup(self.upstream.server_close)
        self.data_dir = tempfile.TemporaryDirectory(prefix='兼容 数据 ')
        self.addCleanup(self.data_dir.cleanup)
        self.state_dir = tempfile.TemporaryDirectory(prefix='状态 目录 ')
        self.addCleanup(self.state_dir.cleanup)
        self.state_env = patch.dict(
            os.environ, {'MINERU_PORTABLE_SEAL_STATE': str(Path(self.state_dir.name) / 'seal-clean.txt')})
        self.state_env.start()
        self.addCleanup(self.state_env.stop)
        import mineru_seal_clean

        mineru_seal_clean._STATE_CACHE = None
        self.server = compat.create_server(
            '127.0.0.1', 0, f'http://127.0.0.1:{self.upstream.server_address[1]}', 'test-key', self.data_dir.name)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def request(self, method, path, body=None, headers=None):
        request = urllib.request.Request(self.base + path, data=body, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_page_and_capabilities(self):
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        page = body.decode('utf-8')
        self.assertIn('兼容旧浏览器', page)
        self.assertIn('/api/capabilities', page)
        # 页面脚本必须避开旧内核不支持的特性
        for forbidden in ('??=', 'structuredClone', 'replaceAll', '=>'):
            self.assertNotIn(forbidden, page, f'页面脚本不应出现 {forbidden}')
        for label in ('DOCX', 'LaTeX', 'EPUB', 'PDF'):
            self.assertIn(label, page, f'页面应提供 {label} 的本地渲染导出入口')
        for pushed in ("formats.push('html'", "formats.push('docx'", "formats.push('latex'"):
            self.assertNotIn(pushed, page, '本地服务不接受的格式不能进入解析请求')

        status, _, body = self.request('GET', '/api/capabilities')
        payload = json.loads(body.decode('utf-8'))
        self.assertEqual([tier['id'] for tier in payload['tiers']], ['standard', 'advanced'])
        self.assertEqual(payload['default_tier'], 'standard')
        self.assertTrue(payload['seal_clean'])

    def test_upload_preview_and_parse_forwarding(self):
        content = b'%PDF-1.4 fake bytes'
        status, _, body = self.request('POST', '/api/upload', body=content,
                                       headers={'X-Filename': urllib.parse.quote('测试 文档.pdf')})
        self.assertEqual(status, 200)
        upload_id = json.loads(body.decode('utf-8'))['upload_id']

        status, headers, preview = self.request('GET', f'/api/preview?id={upload_id}')
        self.assertEqual(status, 200)
        self.assertEqual(preview, content)
        self.assertIn('application/pdf', headers['Content-Type'])

        payload = json.dumps({
            'upload_id': upload_id, 'tier': 'advanced', 'force_ocr': True,
            'page_range': '1-3', 'output_formats': ['markdown', 'structured_content', 'zip'],
        }).encode('utf-8')
        status, _, body = self.request('POST', '/api/parse', body=payload, headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode('utf-8'))['task_id'], 'task-1')

        seen = FakeUpstream.seen[-1]
        self.assertEqual(seen['body'], content)
        self.assertEqual(seen['content_type'], 'application/octet-stream')
        self.assertEqual(seen['authorization'], 'Bearer test-key')
        self.assertEqual(seen['query']['filename'], ['测试 文档.pdf'])
        self.assertEqual(seen['query']['tier'], ['advanced'])
        self.assertEqual(seen['query']['ocr_mode'], ['ocr'])
        self.assertEqual(seen['query']['page_range'], ['1-3'])
        self.assertEqual(seen['query']['output_formats'], ['markdown', 'structured_content', 'zip'])

    def test_result_rewrites_markdown_images_and_serves_assets(self):
        status, _, body = self.request('GET', '/api/task/task-1')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode('utf-8'))['status'], 'completed')

        status, _, body = self.request('GET', '/api/task/task-1/result')
        self.assertEqual(status, 200)
        markdown = json.loads(body.decode('utf-8'))['files'][0]['content']['markdown']
        self.assertIn('/api/asset/task-1/images/figure.jpg', markdown)
        self.assertNotIn('](images/', markdown)

        status, headers, asset = self.request('GET', '/api/asset/task-1/images/figure.jpg')
        self.assertEqual(status, 200)
        self.assertEqual(asset, b'\xff\xd8\xff\xe0fake-jpeg')

        status, headers, data = self.request('GET', '/api/file/md-1?name=' + urllib.parse.quote('a.md'))
        self.assertEqual(status, 200)
        self.assertIn('a.md', headers['Content-Disposition'])
        self.assertIn('CPU OCR 1234.56', data.decode('utf-8'))

        status, headers, _ = self.request('GET', '/api/file/md-1?name=' + urllib.parse.quote('中文 文件.md'))
        self.assertEqual(status, 200)
        self.assertIn("filename*=UTF-8''" + urllib.parse.quote('中文 文件.md'),
                      headers['Content-Disposition'])

    def test_cancel_and_seal_switch(self):
        status, _, _ = self.request('POST', '/api/task/task-1/cancel')
        self.assertEqual(status, 200)

        status, _, body = self.request('POST', '/api/seal-clean', body=b'{"enabled": false}',
                                       headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body.decode('utf-8'))['enabled'])
        status, _, body = self.request('GET', '/api/seal-clean')
        self.assertFalse(json.loads(body.decode('utf-8'))['enabled'])

    def test_default_formats_match_local_api_support(self):
        """未指定产物格式时必须只请求本地服务接受的四种，否则整单会被拒。"""
        content = b'%PDF-1.4 fake bytes'
        status, _, body = self.request('POST', '/api/upload', body=content,
                                       headers={'X-Filename': urllib.parse.quote('b.pdf')})
        self.assertEqual(status, 200)
        upload_id = json.loads(body.decode('utf-8'))['upload_id']
        payload = json.dumps({'upload_id': upload_id, 'tier': 'standard'}).encode('utf-8')
        status, _, _ = self.request('POST', '/api/parse', body=payload, headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        self.assertEqual(FakeUpstream.seen[-1]['query']['output_formats'],
                         ['markdown', 'middle_json', 'structured_content', 'zip'])

    def _fake_renderer(self, calls):
        class FakeOptions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def fake_render(middle_json, render_format, options=None):
            calls.append((str(render_format), sorted(options.kwargs)))
            if str(render_format) in ('docx', 'epub', 'pdf'):
                return b'RENDERED'
            return '<html>ok</html>'

        return {
            'ParseResult': types.SimpleNamespace(
                from_dict=lambda value: types.SimpleNamespace(middle_json=types.SimpleNamespace())),
            'render': fake_render,
            'markdown': ('markdown', FakeOptions),
            'html': ('html', FakeOptions),
            'latex': ('latex', FakeOptions),
            'docx': ('docx', FakeOptions),
            'epub': ('epub', FakeOptions),
            'pdf': ('pdf', FakeOptions),
        }

    def test_export_renders_locally_with_official_engine(self):
        calls = []
        with patch.object(compat, '_renderer', lambda: self._fake_renderer(calls)):
            status, headers, body = self.request(
                'GET', '/api/export/task-1?format=docx&name=' + urllib.parse.quote('报告.docx'))
        self.assertEqual(status, 200)
        self.assertEqual(body, b'RENDERED')
        self.assertIn('wordprocessingml', headers['Content-Type'])
        # 中文文件名必须走 RFC 5987，否则 latin-1 头编码异常会让整个响应断掉
        self.assertIn("filename*=UTF-8''" + urllib.parse.quote('报告.docx'),
                      headers['Content-Disposition'])
        self.assertEqual(calls[0][0], 'docx')
        self.assertIn('asset_resolver', calls[0][1])

        with patch.object(compat, '_renderer', lambda: self._fake_renderer(calls)):
            status, headers, body = self.request('GET', '/api/export/task-1?format=html')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertEqual(body, b'<html>ok</html>')
        self.assertIn('asset_base_url', calls[1][1])

    def test_export_rejects_unknown_format_and_reports_failure(self):
        status, _, _ = self.request('GET', '/api/export/task-1?format=zipdoc')
        self.assertEqual(status, 400)

        def broken_renderer():
            raise ImportError('no renderer')

        with patch.object(compat, '_renderer', broken_renderer):
            status, _, body = self.request('GET', '/api/export/task-1?format=pdf')
        self.assertEqual(status, 501)
        self.assertIn('本地渲染', body.decode('utf-8'))

    def test_unknown_routes_and_missing_upload(self):
        status, _, body = self.request('GET', '/api/unknown')
        self.assertEqual(status, 404)
        self.assertIn('error', json.loads(body.decode('utf-8')))

        payload = json.dumps({'upload_id': 'deadbeef', 'output_formats': ['markdown']}).encode('utf-8')
        status, _, body = self.request('POST', '/api/parse', body=payload, headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertIn('重新选择文件', body.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
