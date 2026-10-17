"""官方页兼容垫片的单元测试：注入位置、幂等与脚本语法守门。"""
import importlib.util
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGES = Path(__file__).resolve().parents[1] / 'app' / 'packages'


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, PACKAGES / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    module = sys.modules[name]
    spec.loader.exec_module(module)
    return module


seal = load_module('mineru_seal_clean')
compat = load_module('mineru_webui_compat')


def fake_app_module():
    """最小官方构建入口替身：返回带 launch kwargs 的假界面对象。"""

    module = types.ModuleType('mineru.kit.gradio.app')

    def build_gradio_app(*args, **kwargs):
        return types.SimpleNamespace(_mineru_kit_launch_kwargs={'js': '/*official*/', 'css': '/*official css*/'})

    module.build_gradio_app = build_gradio_app
    return module


class LegacyShimTests(unittest.TestCase):
    def setUp(self):
        compat._WARNED.clear()
        self.meta_path = list(sys.meta_path)

    def tearDown(self):
        sys.meta_path[:] = self.meta_path

    def test_shim_is_prepended_before_official_script(self):
        app = fake_app_module()
        with patch.dict(sys.modules, {'mineru.kit.gradio.app': app}):
            self.assertTrue(compat.install())
            demo = app.build_gradio_app()
        script = demo._mineru_kit_launch_kwargs['js']
        self.assertIn(compat.MARKER, script)
        self.assertLess(script.index(compat.MARKER), script.index('/*official*/'))
        self.assertEqual(demo._mineru_kit_launch_kwargs['css'], '/*official css*/')

    def test_injection_is_idempotent(self):
        demo = types.SimpleNamespace(_mineru_kit_launch_kwargs={'js': '/*official*/'})
        self.assertTrue(compat.inject(demo))
        self.assertFalse(compat.inject(demo))
        self.assertEqual(demo._mineru_kit_launch_kwargs['js'].count(compat.MARKER), 1)

    def test_missing_launch_kwargs_is_ignored(self):
        self.assertFalse(compat.inject(types.SimpleNamespace()))

    def test_shim_script_avoids_features_missing_in_legacy_chromium(self):
        script = compat.POLYFILL_JS
        for forbidden in ('=>', '??=', 'async ', 'await ', '`', '...'):
            self.assertNotIn(forbidden, script)
        # 只允许在定义与存在性判断处出现这些名字，不允许真的调用
        self.assertNotRegex(script, r'[^.\w]structuredClone\(')
        self.assertNotRegex(script, r'[^.\w]replaceAll\(')
        self.assertNotRegex(script, r'replaceAll\s*\)')
        self.assertNotIn('.at(', script.replace('prototype.at', ''))
        self.assertNotRegex(script, r'\bnew Promise\.any')

    def test_shim_coexists_with_seal_switch_patch(self):
        """两个补丁都包装官方构建入口时，各自的功能都应生效。"""
        ui = load_module('mineru_seal_clean_ui')
        ui._INSTALLED = False
        ui._WARNED.clear()
        app = fake_app_module()
        with patch.dict(sys.modules, {'mineru.kit.gradio.app': app}):
            self.assertTrue(ui.install())
            self.assertTrue(compat.install())
            demo = app.build_gradio_app()
        self.assertIn(compat.MARKER, demo._mineru_kit_launch_kwargs['js'])
        # 界面补丁在没有 gradio 的进程里只记录告警，不影响构建结果
        self.assertTrue(demo is not None)

    def test_both_patches_apply_when_target_module_imports_later(self):
        """回归：两个补丁登记同一个尚未导入的模块时，不能互相抢占导入钩子。

        sitecustomize 先装去章开关补丁、后装兼容垫片，二者目标是同一个模块；
        真实运行中曾因为各插一个 finder，只有后装的垫片生效，开关整个丢失。
        """
        ui = load_module('mineru_seal_clean_ui')
        ui._INSTALLED = False
        ui._WARNED.clear()
        seal._PATCHERS.pop(compat.TARGET_MODULE, None)
        seal._FINDERS.pop(compat.TARGET_MODULE, None)
        saved = {name: value for name, value in sys.modules.items()
                 if name == 'mineru' or name.startswith('mineru.')}
        for name in saved:
            del sys.modules[name]
        with tempfile.TemporaryDirectory(prefix='双补丁 ') as directory:
            root = Path(directory)
            target = root / 'mineru' / 'kit' / 'gradio'
            for package in (root / 'mineru', root / 'mineru' / 'kit', target):
                package.mkdir(parents=True, exist_ok=True)
                (package / '__init__.py').write_text('', encoding='utf-8')
            (target / 'app.py').write_text(
                'def build_gradio_app(*args, **kwargs):\n'
                "    return type('Demo', (), {'_mineru_kit_launch_kwargs': {'js': '/*official*/'}})()\n",
                encoding='utf-8')
            sys.path.insert(0, str(root))
            importlib.invalidate_caches()
            try:
                self.assertTrue(ui.install())
                self.assertTrue(compat.install())
                wrapped = importlib.import_module('mineru.kit.gradio.app').build_gradio_app
                self.assertTrue(getattr(wrapped, '_mineru_portable_seal_ui', False))
                self.assertTrue(getattr(wrapped, '_mineru_portable_legacy_shims', False))
                self.assertIn(compat.MARKER, wrapped()._mineru_kit_launch_kwargs['js'])
            finally:
                sys.path.remove(str(root))
                importlib.invalidate_caches()
                for name in [name for name in sys.modules if name == 'mineru' or name.startswith('mineru.')]:
                    del sys.modules[name]
                sys.modules.update(saved)
                seal._PATCHERS.pop(compat.TARGET_MODULE, None)
                seal._FINDERS.pop(compat.TARGET_MODULE, None)


if __name__ == '__main__':
    unittest.main()
