"""兼容页导出 DOCX 的端到端测试：假上游 + 真实 MinerU 渲染栈。

完整包上的同一条链路由 `tests/smoke_portable.py` 覆盖；这里先把两个关键分支钉住，
免得只能靠一次完整的 CI 才能发现：任务结果里没有 Middle JSON 时必须给出明确错误，
有 Middle JSON 时导出的 DOCX 必须能被 python-docx 打开、含识别文本，并带上图片。
"""
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'app' / 'packages'))

import mineru_docx_fast as fast  # noqa: E402

spec = importlib.util.spec_from_file_location('compat_server', ROOT / 'app' / 'compat_server.py')
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)

MARKER = 'CPU OCR 1234.56'


def load_bench():
    """复用基准脚本里的合成文档构造，避免两处漂移。"""
    bench_spec = importlib.util.spec_from_file_location('bench_docx_export', ROOT / 'tests' / 'bench_docx_export.py')
    module = importlib.util.module_from_spec(bench_spec)
    bench_spec.loader.exec_module(module)
    return module


class FakeUpstream(BaseHTTPRequestHandler):
    """最小上游：只提供导出需要的任务结果与 zip 内容。"""

    document = None
    zipped = b''

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route in ('/v1/tasks/task-1/result', '/v1/tasks/task-missing/result'):
            content = {'markdown': f'## {MARKER}\n'}
            if route.startswith('/v1/tasks/task-1'):
                content['middle_json'] = self.document
            self._json({'task_id': route.split('/')[3], 'status': 'completed', 'files': [
                {'name': 'a.pdf', 'status': 'completed', 'content': content,
                 'output_files': {'zip': {'file_id': 'zip-1'}}}]})
            return
        if route == '/v1/files/zip-1/content':
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(len(self.zipped)))
            self.end_headers()
            self.wfile.write(self.zipped)
            return
        self._json({'error': {'message': 'not found'}}, status=404)


class CompatDocxExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import docvortex.render.docx  # noqa: F401
            import mineru.render  # noqa: F401
        except Exception as exc:
            raise unittest.SkipTest(f'需要 mineru 与 docvortex：{type(exc).__name__}: {exc}')
        import numpy as np
        from PIL import Image

        cls.bench = load_bench()
        pixels = np.random.default_rng(7).integers(0, 256, (800, 600, 3), dtype=np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, 'JPEG', quality=85)
        cls.image_bytes = buffer.getvalue()
        cls.document = cls.bench.build_payload(pages=4, image_paths=['images/figure-1.jpg'])
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('images/figure-1.jpg', cls.image_bytes)
            zipped.writestr('a.md', f'## {MARKER}\n')
        cls.zipped = archive.getvalue()

    def setUp(self):
        fast.install()
        fast.prepare()
        FakeUpstream.document = self.document
        FakeUpstream.zipped = self.zipped
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeUpstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.upstream.shutdown)
        self.addCleanup(self.upstream.server_close)
        self.data_dir = tempfile.TemporaryDirectory(prefix='兼容 导出数据 ')
        self.addCleanup(self.data_dir.cleanup)
        self.server = compat.create_server(
            '127.0.0.1', 0, f'http://127.0.0.1:{self.upstream.server_address[1]}', 'test-key', self.data_dir.name)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def request(self, path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(self.base + path)
        try:
            with opener.open(request, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_export_docx_end_to_end(self):
        name = urllib.parse.quote('报告 一.docx')
        status, headers, body = self.request(f'/api/export/task-1?format=docx&name={name}')
        self.assertEqual(status, 200, body[:300])
        self.assertIn('wordprocessingml', headers['Content-Type'])
        self.assertIn("filename*=UTF-8''" + name, headers['Content-Disposition'])

        from docx import Document

        text = '\n'.join(paragraph.text for paragraph in Document(io.BytesIO(body)).paragraphs)
        # 文本可能被拆进多个 run 或段落，按去空白后的整体判断
        self.assertIn(''.join(MARKER.split()), ''.join(text.split()))
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            media = [info for info in archive.infolist() if info.filename.startswith('word/media/')]
        self.assertTrue(media, '导出的 DOCX 没有带上图片')
        if fast.enabled():
            self.assertEqual([info.compress_type for info in media], [zipfile.ZIP_STORED] * len(media),
                             '图片成员未按补丁原样存放')

    def test_export_without_middle_json_reports_clear_error(self):
        """结果里没有 Middle JSON 时要明确报错，而不是让调用方以为在无限等待。"""
        status, _, body = self.request('/api/export/task-missing?format=docx')
        self.assertEqual(status, 502)
        self.assertIn('Middle JSON', body.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
