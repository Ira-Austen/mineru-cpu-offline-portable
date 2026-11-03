"""DOCX 导出加速补丁的单元测试：只验证接线与缓存语义，不做真实渲染。

补丁作用在第三方库内部（docvortex 的公式/图片模块、python-docx 的包写入器），
这里用最小假模块覆盖这些名字，避免单元测试依赖完整渲染栈；真实渲染与耗时对照
由 ``tests/bench_docx_export.py`` 在 CI 的独立任务里完成。
"""
import os
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app' / 'packages'))

import mineru_docx_fast as fast  # noqa: E402

FAKE_NAMES = (
    'docvortex',
    'docvortex.render',
    'docvortex.render.docx',
    'docvortex.render._internal',
    'docvortex.render._internal.common',
    'docvortex.render._internal.common.context',
    'docvortex.render._internal.docx',
    'docvortex.render._internal.docx.assets',
    'docvortex.render._internal.docx.inline',
    'docvortex.render._internal.docx.math',
    'docvortex.render._internal.docx.renderer',
    'docx',
    'docx.opc',
    'docx.opc.phys_pkg',
)


def fake_module(name, **attributes):
    """造一个只带指定属性的假模块，避免为补丁拉入真实依赖。"""
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def fake_tree(children):
    """按点分名字造出完整假包树，并把子模块挂到父包属性上。

    ``from a.b import c`` 既查 ``sys.modules`` 也取父包属性，缺一个都会 ImportError，
    所以补丁用到的每个模块都要连同各级父包一起准备好。
    """
    modules = {}
    for name in children:
        parts = name.split('.')
        for depth in range(1, len(parts) + 1):
            modules.setdefault('.'.join(parts[:depth]), None)
    for name in modules:
        modules[name] = fake_module(name)
    for name, attributes in children.items():
        for key, value in attributes.items():
            setattr(modules[name], key, value)
    for name in children:
        parent, _, child = name.rpartition('.')
        if parent in modules:
            setattr(modules[parent], child, modules[name])
    return modules


@contextmanager
def patched_modules(modules):
    """把假模块塞进 sys.modules，退出时还原。"""
    saved = {name: sys.modules.get(name) for name in set(FAKE_NAMES) | set(modules)}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class SwitchTests(unittest.TestCase):
    def test_switches_and_limits(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MINERU_PORTABLE_DOCX_FAST', None)
            self.assertTrue(fast.enabled())
            os.environ['MINERU_PORTABLE_DOCX_FAST'] = '0'
            self.assertFalse(fast.enabled())
            self.assertFalse(fast.owned_document_enabled())
            os.environ['MINERU_PORTABLE_DOCX_FAST'] = '1'
            os.environ.pop('MINERU_PORTABLE_DOCX_OWNED', None)
            self.assertTrue(fast.owned_document_enabled())
            os.environ['MINERU_PORTABLE_DOCX_OWNED'] = 'off'
            self.assertFalse(fast.owned_document_enabled())
            os.environ['MINERU_PORTABLE_DOCX_FORMULA_CACHE'] = '2'
            self.assertEqual(fast._limit(fast.ENV_FORMULA_CACHE, 512), 2)
            os.environ['MINERU_PORTABLE_DOCX_FORMULA_CACHE'] = '不是数字'
            self.assertEqual(fast._limit(fast.ENV_FORMULA_CACHE, 512), 512)

    def test_limit_reached(self):
        self.assertFalse(fast.limit_reached({}, 0))
        self.assertFalse(fast.limit_reached({'a': 1}, 2))
        self.assertTrue(fast.limit_reached({'a': 1, 'b': 2}, 2))


class FormulaCacheTests(unittest.TestCase):
    def test_repeats_hit_cache_and_never_share_nodes(self):
        from lxml import etree

        calls = []

        def original(latex, *, display=False):
            calls.append((latex, display))
            element = etree.Element('{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath')
            element.text = latex
            return element

        modules = fake_tree({
            'docvortex.render._internal.docx.math': {'latex_to_omml': original},
            'docvortex.render._internal.docx.inline': {'latex_to_omml': original},
            'docvortex.render._internal.docx.renderer': {'latex_to_omml': original},
        })
        math_module = modules['docvortex.render._internal.docx.math']
        with patched_modules(modules):
            self.assertTrue(fast.install_formula_cache())
            self.assertFalse(fast.install_formula_cache())
            first = math_module.latex_to_omml('x^{2}', display=True)
            second = math_module.latex_to_omml('x^{2}', display=True)
            third = math_module.latex_to_omml('x^{2}', display=False)
            # 消费方在导入时就绑定了函数名，必须一并换成包装后的版本
            self.assertIs(modules['docvortex.render._internal.docx.inline'].latex_to_omml,
                          math_module.latex_to_omml)
            self.assertIs(modules['docvortex.render._internal.docx.renderer'].latex_to_omml,
                          math_module.latex_to_omml)

        self.assertEqual(calls, [('x^{2}', True), ('x^{2}', False)])
        self.assertEqual(first.text, second.text)
        self.assertIsNot(first, second)
        # 调用方会就地改节点（居中、剥离 oMath），缓存绝不能把同一个节点交两次
        first.set('changed', '1')
        self.assertIsNone(second.get('changed'))
        self.assertEqual(third.text, 'x^{2}')


class ImageCacheTests(unittest.TestCase):
    def test_same_bytes_decode_once(self):
        calls = []

        def original(data, *, declared_extension=None):
            calls.append((data, declared_extension))
            return SimpleNamespace(data=data, width_px=8, height_px=6, extension=declared_extension or 'png')

        modules = fake_tree({'docvortex.render._internal.docx.assets': {'prepare_image_bytes': original}})
        assets = modules['docvortex.render._internal.docx.assets']
        with patched_modules(modules):
            self.assertTrue(fast.install_image_cache())
            self.assertFalse(fast.install_image_cache())
            first = assets.prepare_image_bytes(b'aaa', declared_extension='jpg')
            second = assets.prepare_image_bytes(b'aaa', declared_extension='jpg')
            third = assets.prepare_image_bytes(b'aaa', declared_extension='png')
            fourth = assets.prepare_image_bytes(b'bbb', declared_extension='jpg')
        self.assertEqual([call[0] for call in calls], [b'aaa', b'aaa', b'bbb'])
        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertIsNot(first, fourth)


class ZipCompressionTests(unittest.TestCase):
    class RecordingZip:
        """记录 writestr 参数，并真实写盘以便校验压缩方式。"""

        def __init__(self, path):
            self.calls = []
            self._archive = zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED)

        def writestr(self, member, blob, **kwargs):
            self.calls.append((member, kwargs))
            return self._archive.writestr(member, blob, **kwargs)

        def close(self):
            self._archive.close()

    @staticmethod
    def make_writer_class():
        """每次都给一个全新的写入器类，避免补丁标记在测试之间互相污染。"""

        class FakeWriter:
            """按 python-docx 1.2.0 的 _ZipPkgWriter 形态写成员。"""

            def __init__(self, archive):
                self._zipf = archive

            def write(self, pack_uri, blob):
                self._zipf.writestr(pack_uri.membername, blob)

        return FakeWriter

    def write_package(self, directory, environment):
        """在给定环境下导出两个成员的压缩参数并返回记录。"""
        from contextlib import nullcontext

        path = Path(directory) / 'package.docx'
        recorder = self.RecordingZip(path)
        writer_class = self.make_writer_class()
        writer = writer_class(recorder)
        modules = fake_tree({'docx.opc.phys_pkg': {'_ZipPkgWriter': writer_class}})
        context = mock.patch.dict(os.environ, environment) if environment is not None else nullcontext()
        with patched_modules(modules), context:
            if environment is not None and not environment:
                os.environ.pop(fast.ENV_ZIP_LEVEL, None)
            self.assertTrue(fast.install_zip_compression())
            writer.write(SimpleNamespace(membername='word/media/image1.jpg'), b'\xff\xd8jpeg-bytes')
            writer.write(SimpleNamespace(membername='word/document.xml'), b'<w:document/>' * 4)
        recorder.close()
        return path, recorder

    def test_media_stored_and_xml_uses_low_level(self):
        with tempfile.TemporaryDirectory(prefix='docx 压缩 ') as directory:
            path, recorder = self.write_package(directory, {fast.ENV_ZIP_LEVEL: '1'})
            self.assertEqual(recorder.calls[0], ('word/media/image1.jpg', {'compress_type': zipfile.ZIP_STORED}))
            self.assertEqual(recorder.calls[1], ('word/document.xml', {'compresslevel': 1}))
            with zipfile.ZipFile(path) as archive:
                types_by_name = {info.filename: info.compress_type for info in archive.infolist()}
            self.assertEqual(types_by_name['word/media/image1.jpg'], zipfile.ZIP_STORED)
            self.assertEqual(types_by_name['word/document.xml'], zipfile.ZIP_DEFLATED)

    def test_default_level_keeps_upstream_xml_compression(self):
        """默认时图片原样存放，XML 仍按 python-docx 的默认级别压缩，体积不额外膨胀。"""
        with tempfile.TemporaryDirectory(prefix='docx 默认 ') as directory:
            _, recorder = self.write_package(directory, {})
            self.assertEqual(recorder.calls[0], ('word/media/image1.jpg', {'compress_type': zipfile.ZIP_STORED}))
            self.assertEqual(recorder.calls[1], ('word/document.xml', {}))


class StyleCacheTests(unittest.TestCase):
    def install(self):
        """装上样式缓存补丁，返回被包装的样式类与调用记录。"""
        calls = []
        state = {'value': SimpleNamespace(name='Normal'), 'style_id': 'Normal'}

        class FakeStyles:
            def default_for(self, style_type):
                calls.append(style_type)
                return state['value']

            def _get_style_id_from_name(self, style_name, style_type):
                calls.append(('name', style_name, style_type))
                return state['style_id']

            def add_style(self, *args, **kwargs):
                calls.append('add_style')
                return None

        modules = fake_tree({'docx.oxml.styles': {'CT_Styles': FakeStyles},
                             'docx.styles.styles': {'Styles': FakeStyles}})
        self.enterContext(patched_modules(modules))
        self.assertTrue(fast.install_style_cache())
        self.assertFalse(fast.install_style_cache())
        return modules['docx.oxml.styles'].CT_Styles, calls, state

    def test_default_style_id_none_is_cached(self):
        """正文样式就是默认样式，python-docx 会返回 None；这个答案也必须缓存。"""
        styles_class, calls, state = self.install()
        state['style_id'] = None
        document = SimpleNamespace()
        self.assertIsNone(styles_class._get_style_id_from_name(document, 'Normal', 'paragraph'))
        self.assertIsNone(styles_class._get_style_id_from_name(document, 'Normal', 'paragraph'))
        self.assertEqual(calls, [('name', 'Normal', 'paragraph')])
        # 新增样式后缓存失效，必须重新查询
        styles_class.add_style(document, '新样式', 'paragraph')
        styles_class._get_style_id_from_name(document, 'Normal', 'paragraph')
        self.assertEqual(calls, [('name', 'Normal', 'paragraph'), 'add_style', ('name', 'Normal', 'paragraph')])

    def test_style_id_lookup_by_name_is_reused(self):
        """按名字查样式 id 同样按文档缓存，重复段落不再扫样式表。"""
        styles_class, calls, _ = self.install()
        document = SimpleNamespace()
        other = SimpleNamespace()
        first = styles_class._get_style_id_from_name(document, 'Normal', 'paragraph')
        second = styles_class._get_style_id_from_name(document, 'Normal', 'paragraph')
        styles_class._get_style_id_from_name(other, 'Normal', 'paragraph')
        self.assertEqual(calls, [('name', 'Normal', 'paragraph'), ('name', 'Normal', 'paragraph')])
        self.assertEqual(first, second)
        self.assertEqual(first, 'Normal')

    def test_default_style_lookup_is_reused(self):
        styles_class, calls, _ = self.install()
        document = SimpleNamespace()
        other = SimpleNamespace()
        first = styles_class.default_for(document, 'paragraph')
        second = styles_class.default_for(document, 'paragraph')
        third = styles_class.default_for(document, 'character')
        styles_class.default_for(other, 'paragraph')
        # 同一文档同一类型只查一次；不同类型、不同文档各自算一次
        self.assertEqual(calls, ['paragraph', 'character', 'paragraph'])
        self.assertIs(first, second)
        self.assertEqual(third.name, 'Normal')

    def test_none_is_not_cached_and_new_style_clears_cache(self):
        styles_class, calls, state = self.install()
        document = SimpleNamespace()
        styles_class.default_for(document, 'paragraph')
        styles_class.default_for(document, 'paragraph')
        self.assertEqual(calls, ['paragraph'])
        # 新增样式后必须重新查询，否则会拿到过期的默认样式
        styles_class.add_style(document, '自定义', 'paragraph')
        styles_class.default_for(document, 'paragraph')
        self.assertEqual(calls, ['paragraph', 'add_style', 'paragraph'])
        # 查不到默认样式时不缓存，避免把"暂无"固化下来（换一个没有缓存条目的文档）
        state['value'] = None
        calls.clear()
        fresh = SimpleNamespace()
        self.assertIsNone(styles_class.default_for(fresh, 'paragraph'))
        self.assertIsNone(styles_class.default_for(fresh, 'paragraph'))
        self.assertEqual(calls, ['paragraph', 'paragraph'])


class EntryWiringTests(unittest.TestCase):
    def test_install_is_idempotent_in_this_process(self):
        """本进程可能已由 sitecustomize 装过补丁，所以只要求幂等与钩子已登记。"""
        loaded = 'docvortex.render._internal.docx.renderer' in sys.modules
        fast.install()
        self.assertFalse(fast.install())
        self.assertIn(fast.TARGET_MODULE, fast.seal._PATCHERS)
        if not loaded:
            self.assertNotIn('docvortex.render._internal.docx.renderer', sys.modules)

    def test_install_in_fresh_process_takes_effect_once(self):
        """禁用 site 的新进程里没有 sitecustomize 抢先安装，首次安装必须生效。"""
        script = (
            'import sys\n'
            f'sys.path.insert(0, {str(ROOT / "app" / "packages")!r})\n'
            'import mineru_docx_fast as fast\n'
            'assert fast.install() is True, "首次安装应生效"\n'
            'assert fast.install() is False, "重复安装应返回 False"\n'
            'assert fast.TARGET_MODULE in fast.seal._PATCHERS\n'
            'assert "docvortex.render._internal.docx.renderer" not in sys.modules, "不应提前导入渲染栈"\n'
            'print("fresh-ok")\n'
        )
        # -S 跳过 site：否则 PYTHONPATH 上的 sitecustomize 会先装一次补丁
        result = subprocess.run([sys.executable, '-S', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('fresh-ok', result.stdout)

    def test_disabled_switch_skips_install(self):
        with mock.patch.object(fast, '_INSTALLED', False), mock.patch.dict(
                os.environ, {fast.ENV_ENABLE: '0'}):
            self.assertFalse(fast.install())

    def test_disabled_switch_skips_patch(self):
        facade = fake_module('docvortex.render.docx', render_docx=lambda *a, **k: b'')
        with patched_modules({'docvortex.render.docx': facade}), mock.patch.dict(
                os.environ, {fast.ENV_ENABLE: '0'}):
            self.assertFalse(fast._patch_module(facade))

    def _patched_entry(self, **environment):
        """返回（包装后的入口, 记录），用真实的 docvortex 所有权上下文验证接线。"""
        from docvortex.render._internal.common import context as real_context

        calls = {}

        def original(middle_json, *, asset_resolver=None):
            calls['owned'] = real_context.claim_owned_document(middle_json)
            calls['resolver'] = asset_resolver
            return b'RENDERED'

        facade = fake_module('docvortex.render.docx', render_docx=original)
        self.enterContext(patched_modules({'docvortex.render.docx': facade}))
        self.enterContext(mock.patch.object(fast, '_owned_document', real_context.owned_render_document))
        self.enterContext(mock.patch.dict(os.environ, environment))
        self.assertTrue(fast._patch_module(facade))
        self.assertFalse(fast._patch_module(facade))
        return facade.render_docx, calls

    def test_render_entry_claims_owned_document(self):
        try:
            entry, calls = self._patched_entry()
        except ImportError as exc:  # 无 docvortex 时跳过，真实渲染由基准脚本覆盖
            self.skipTest(f'需要 docvortex：{exc}')
        resolver = object()
        self.assertEqual(entry(SimpleNamespace(), asset_resolver=resolver), b'RENDERED')
        self.assertTrue(calls['owned'])
        self.assertIs(calls['resolver'], resolver)

    def test_render_entry_keeps_upstream_behaviour_when_disabled(self):
        try:
            entry, calls = self._patched_entry(**{fast.ENV_OWNED: '0'})
        except ImportError as exc:
            self.skipTest(f'需要 docvortex：{exc}')
        self.assertEqual(entry(SimpleNamespace()), b'RENDERED')
        self.assertFalse(calls['owned'])


if __name__ == '__main__':
    unittest.main()
