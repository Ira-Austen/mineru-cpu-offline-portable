import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / 'app' / 'launcher.py'
spec = importlib.util.spec_from_file_location('portable_launcher', source)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class PortableTests(unittest.TestCase):
    def test_relocation_and_unicode_model_paths(self):
        with tempfile.TemporaryDirectory(prefix='中文 空格 ') as directory:
            root = Path(directory)
            for name in ('主模型.gguf', '视觉模型.gguf'):
                (root / name).write_bytes(b'GGUF' + b'\0' * 20)
            with patch.object(launcher, 'ROOT', root):
                paths = launcher.resolve_models({'gguf': '主模型.gguf', 'mmproj': '视觉模型.gguf'})
                self.assertTrue(all(path.is_file() for path in paths))

    def test_missing_projector_fails_before_start(self):
        with self.assertRaises(ValueError):
            launcher.resolve_models({'gguf': 'missing.gguf', 'mmproj': ''})

    def test_environment_removes_inherited_remote_address(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher, 'ROOT', Path(directory)), patch.dict('os.environ',
                    {'MINERU_MODEL_VLM_SERVER_URL': 'https://external.invalid',
                     'MINERU_CONFIG': 'unexpected.yaml', 'LLAMA_ARG_N_GPU_LAYERS': '99'}):
                env = launcher.environment({'threads': 4})
                self.assertEqual(env['MINERU_MODEL_VLM_SERVER_URL'], 'http://127.0.0.1:30000')
                self.assertNotIn('MINERU_CONFIG', env)
                self.assertNotIn('LLAMA_ARG_N_GPU_LAYERS', env)
                self.assertEqual(env['MINERU_MODEL_SOURCE'], 'local')
                self.assertEqual(env['MINERU_PORTABLE_OFFLINE'], '1')

    def test_environment_configures_poll_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher, 'ROOT', Path(directory)):
                env = launcher.environment({'threads': 4})
                self.assertEqual(env['MINERU_POLL_TIMEOUT'], '86400')
                env_custom = launcher.environment({'threads': 4, 'poll_timeout': 14400})
                self.assertEqual(env_custom['MINERU_POLL_TIMEOUT'], '14400')

    def test_environment_switches_seal_cleaning(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher, 'ROOT', Path(directory)):
                env = launcher.environment({'threads': 4})
                self.assertEqual(env['MINERU_PORTABLE_SEAL_CLEAN'], '1')
                disabled = launcher.environment({'threads': 4, 'seal_clean': False})
                self.assertEqual(disabled['MINERU_PORTABLE_SEAL_CLEAN'], '0')

    def test_seal_state_file_is_shared_with_webui(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher, 'ROOT', Path(directory)):
                self.assertIsNone(launcher.read_seal_state())
                launcher.write_seal_state(False)
                self.assertFalse(launcher.read_seal_state())
                launcher.write_seal_state(True)
                self.assertTrue(launcher.read_seal_state())
                env = launcher.environment({'threads': 4})
                self.assertEqual(env['MINERU_PORTABLE_SEAL_STATE'],
                                 str(Path(directory) / 'data' / 'seal-clean.txt'))

    def test_compat_page_port_and_local_key(self):
        self.assertEqual(launcher.COMPAT_PORT, 7861)
        self.assertIn(launcher.COMPAT_PORT, launcher.PORTS)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher, 'ROOT', Path(directory)):
                env = launcher.environment({'threads': 4})
                self.assertEqual(env['MINERU_API_KEY'], launcher.LOCAL_API_KEY)
                self.assertEqual(env['MINERU_PORTABLE_API_URL'], 'http://127.0.0.1:8000')

    def test_smoke_uses_the_same_local_api_key(self):
        """smoke 直连 /v1 必须带包内固定 key，且不能和 launcher 各写一份常量。

        api-server 配置了 key 后，除 /v1/health、/v1/tiers 等公共路径外都要求
        Authorization；测试脚本漏带会整包 401（CI 曾因此失败）。
        """
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('smoke_portable', root / 'tests' / 'smoke_portable.py')
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        self.assertEqual(smoke.local_api_key(root / 'app'), launcher.LOCAL_API_KEY)

    def test_settings_page_exposes_seal_cleaning_switch(self):
        self.assertIn('name="seal_clean"', launcher.PAGE)
        self.assertIn('{seal_clean_checked}', launcher.PAGE)

    def test_settings_page_links_to_compat_page(self):
        self.assertIn(f'http://127.0.0.1:{launcher.COMPAT_PORT}/', launcher.PAGE)
        self.assertIn('旧浏览器兼容页', launcher.PAGE)

    def test_external_onnx_is_independent_of_writable_state(self):
        with tempfile.TemporaryDirectory(prefix='ONNX 中文 空格 ') as directory:
            root = Path(directory)
            external = root / '已下载模型'
            (external / 'Layout').mkdir(parents=True)
            app = root / '新运行时'
            with patch.object(launcher, 'ROOT', app):
                env = launcher.environment({'threads': 4, 'onnx_dir': str(external)})
                self.assertEqual(env['MINERU_PORTABLE_ONNX_DIR'], str(external))
                self.assertEqual(env['MINERU_MODEL_BASE_DIR'], str(app / 'data' / 'model-state'))
                self.assertFalse((external / '.locks').exists())

    def test_onnx_accepts_model_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            nested = parent / 'MinerU-4_models_onnx'
            (nested / 'Layout').mkdir(parents=True)
            self.assertEqual(launcher.onnx_directory({'onnx_dir': str(parent)}), nested)

    def test_missing_onnx_fails_before_services_start(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'ONNX 目录缺少必要文件'):
                launcher.check_onnx({'onnx_dir': directory})


if __name__ == '__main__':
    unittest.main()
