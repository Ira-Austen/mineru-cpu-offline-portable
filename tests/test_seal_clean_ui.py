"""WebUI 去章开关补丁的单元测试：插入位置、兜底、状态写入与失败隔离。

伪 gradio 模块复刻了真实 Blocks 的关键语义：布局容器进入/退出时维护渲染上下文，
组件初始化时挂到当前上下文；补丁正是在容器退出前插入控件。
"""
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGES = Path(__file__).resolve().parents[1] / 'app' / 'packages'
STACK = []


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, PACKAGES / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seal = load_module('mineru_seal_clean')
ui = load_module('mineru_seal_clean_ui')


class FakeBlock:
    def __init__(self, *args, elem_classes=None, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.elem_classes = list(elem_classes or [])
        self.children = []
        if STACK:
            STACK[-1].children.append(self)


class FakeColumn(FakeBlock):
    def __enter__(self):
        STACK.append(self)
        return self

    def __exit__(self, *args):
        STACK.pop()
        return False


class FakeBlocks(FakeBlock):
    def __enter__(self):
        STACK.append(self)
        return self

    def __exit__(self, *args):
        STACK.pop()
        return False


class FakeCheckbox(FakeBlock):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.change_calls = []

    def change(self, **kwargs):
        self.change_calls.append(kwargs)


class FakeMarkdown(FakeBlock):
    pass


def fake_gradio(checkbox=None):
    module = types.ModuleType('gradio')
    module.Column = type('Column', (FakeColumn,), {})
    module.Blocks = type('Blocks', (FakeBlocks,), {})
    module.Checkbox = checkbox or FakeCheckbox
    module.Markdown = FakeMarkdown
    return module


def fake_app_module(include_control_column=True):
    module = types.ModuleType('mineru.kit.gradio.app')

    def build_gradio_app(*args, **kwargs):
        import gradio as gr

        with gr.Blocks() as demo:
            if include_control_column:
                with gr.Column(elem_classes=['mineru-kit-control', 'mineru-control-column']):
                    gr.Checkbox(label='force ocr')
            with gr.Column(elem_classes=['mineru-markdown-pane']):
                gr.Markdown('结果')
        return demo

    module.build_gradio_app = build_gradio_app
    return module


class SealCleanUiTests(unittest.TestCase):
    def setUp(self):
        STACK.clear()
        ui._WARNED.clear()
        ui._INSTALLED = False
        self.meta_path = list(sys.meta_path)
        self.state_dir = tempfile.TemporaryDirectory(prefix='状态 目录 ')
        self.addCleanup(self.state_dir.cleanup)

    def tearDown(self):
        STACK.clear()
        sys.meta_path[:] = self.meta_path

    def run_app(self, include_control_column=True, checkbox=None):
        gradio = fake_gradio(checkbox)
        app = fake_app_module(include_control_column)
        with patch.dict(sys.modules, {'gradio': gradio, 'mineru.kit.gradio.app': app}):
            self.assertTrue(ui.install())
            demo = app.build_gradio_app()
        return gradio, demo

    def test_switch_is_inserted_into_control_column(self):
        gradio, demo = self.run_app()
        control_column = demo.children[0]
        self.assertIn('mineru-control-column', control_column.elem_classes)
        checkboxes = [child for child in control_column.children if isinstance(child, FakeCheckbox)]
        self.assertEqual(len(checkboxes), 2)  # 官方 force_ocr + 便携包开关
        added = checkboxes[-1]
        self.assertEqual(added.kwargs['label'], ui.LABEL)
        self.assertEqual(len(added.change_calls), 1)
        self.assertEqual(added.change_calls[0]['inputs'], [added])
        self.assertEqual(len(added.change_calls[0]['outputs']), 1)
        self.assertNotIn('__exit__', vars(gradio.Column))  # 布局钩子用完即还原
        self.assertNotIn('__exit__', vars(gradio.Blocks))

    def test_missing_control_column_falls_back_to_root(self):
        _, demo = self.run_app(include_control_column=False)
        checkboxes = [child for child in demo.children if isinstance(child, FakeCheckbox)]
        self.assertEqual(len(checkboxes), 1)
        self.assertEqual(checkboxes[0].kwargs['label'], ui.LABEL)

    def test_apply_state_writes_shared_state_file(self):
        state_file = Path(self.state_dir.name) / 'seal-clean.txt'
        with patch.dict(os.environ, {seal.STATE_ENV: str(state_file)}):
            seal._STATE_CACHE = None
            self.assertTrue(ui.initial_state())
            self.assertIn('已关闭', ui.apply_state(False))
            self.assertEqual(state_file.read_text(encoding='utf-8'), '0')
            self.assertFalse(ui.initial_state())
            self.assertIn('已开启', ui.apply_state(True))
            self.assertEqual(state_file.read_text(encoding='utf-8'), '1')

    def test_ui_failure_does_not_break_app_build(self):
        class BrokenCheckbox(FakeCheckbox):
            """只让便携包新增的开关失败，官方复选框照常创建。"""

            def __init__(self, **kwargs):
                if kwargs.get('label') == ui.LABEL:
                    raise RuntimeError('boom')
                super().__init__(**kwargs)

        _, demo = self.run_app(checkbox=BrokenCheckbox)
        self.assertTrue(demo.children)  # 官方界面照旧构建完成
        self.assertTrue(ui._WARNED)  # 失败被记录，不影响启动

    def test_install_wraps_build_entry_once(self):
        gradio_app = fake_app_module()
        original = gradio_app.build_gradio_app
        with patch.dict(sys.modules, {'mineru.kit.gradio.app': gradio_app}):
            self.assertTrue(ui.install())
            wrapped = gradio_app.build_gradio_app
            self.assertIsNot(wrapped, original)
            self.assertTrue(getattr(wrapped, '_mineru_portable_seal_ui', False))
            ui._INSTALLED = False
            self.assertTrue(ui.install())
            self.assertIs(gradio_app.build_gradio_app, wrapped)


if __name__ == '__main__':
    unittest.main()
