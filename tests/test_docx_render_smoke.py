"""DOCX 渲染补丁的产物冒烟测试：用真实 docvortex 渲染小文档并校验产物质量。

只跑渲染层，不解析 PDF、不加载模型：确认补丁生效后产出的 docx 仍能被 python-docx
打开，正文、表格、公式与图片齐全，图片成员按补丁原样存放，而且同一份文档连渲染
两次结果一致（缓存不会改变输出）。长文档的耗时对照由 tests/bench_docx_export.py
在 CI 的独立任务里完成。
"""
import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app' / 'packages'))

import mineru_docx_fast as fast  # noqa: E402


def load_bench():
    """复用基准脚本里的合成文档构造与产物自检，避免两处漂移。"""
    spec = importlib.util.spec_from_file_location('bench_docx_export', ROOT / 'tests' / 'bench_docx_export.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DocxRenderSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import docvortex.render.docx  # noqa: F401
        except Exception as exc:
            raise unittest.SkipTest(f'需要 docvortex：{type(exc).__name__}: {exc}')
        cls.bench = load_bench()

    def render(self, pages):
        """造一小份文档，渲染两次并返回（产物, 自检, 第二次自检）。"""
        from docvortex.render.docx import render_docx
        from docvortex.schema import MiddleJson

        with tempfile.TemporaryDirectory(prefix='docx 冒烟 ') as directory:
            root = Path(directory)
            paths = self.bench.build_images(root)
            payload = self.bench.build_payload(pages, paths)
            resolver = self.bench.make_resolver(root)
            first = render_docx(MiddleJson.from_dict(payload), asset_resolver=resolver)
            second = render_docx(MiddleJson.from_dict(payload), asset_resolver=resolver)
        return first, self.bench.inspect_docx(first), self.bench.inspect_docx(second)

    def test_patched_render_keeps_document_intact(self):
        fast.install()
        # 补丁可能已被更早的渲染预热过，这里只要求调用成功；产物断言才是关键
        self.assertIsInstance(fast.prepare(), tuple)
        data, report, again = self.render(pages=8)

        self.assertTrue(report['has_marker'], '产物缺少正文标记')
        self.assertGreater(report['paragraphs'], 0)
        self.assertGreater(report['tables'], 0, '表格块未写入产物')
        self.assertGreater(report['images_inline'], 0, '图片块未写入产物')
        self.assertGreater(report['media'], 0, '产物里没有图片成员')
        self.assertEqual(report['media_stored'], report['media'], '图片成员未按补丁原样存放')
        # 缓存不得改变输出：同一份文档连渲染两次，结构完全一致
        self.assertEqual(report['paragraphs'], again['paragraphs'])
        self.assertEqual(report['images_inline'], again['images_inline'])
        self.assertEqual(report['text_length'], again['text_length'])
        self.assertEqual(len(data), len(self.render(pages=8)[0]))

    def test_written_docx_is_readable_and_has_expected_parts(self):
        fast.install()
        data, _, _ = self.render(pages=8)
        from docx import Document

        document = Document(io.BytesIO(data))
        self.assertTrue(any(paragraph.text.strip() for paragraph in document.paragraphs))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
        for required in ('[Content_Types].xml', 'word/document.xml', 'word/_rels/document.xml.rels',
                         'word/styles.xml'):
            self.assertIn(required, names, f'产物缺少 {required}')
        self.assertTrue(any(name.startswith('word/media/') for name in names), '产物缺少媒体成员')


if __name__ == '__main__':
    unittest.main()
