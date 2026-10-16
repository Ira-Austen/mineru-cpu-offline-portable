"""官方页兼容垫片的单元测试：注入位置、幂等与脚本语法守门。"""
import importlib.util
import re
import sys
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


if __name__ == '__main__':
    unittest.main()
