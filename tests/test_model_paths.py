import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / 'app' / 'model_paths.py'
spec = importlib.util.spec_from_file_location('model_paths', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ModelPathsTests(unittest.TestCase):
    def test_model_paths_and_lock_are_separate(self):
        from mineru.model.download import ModelRepo
        from mineru.model.registry import MINERU_4_MODELS_ONNX
        with tempfile.TemporaryDirectory(prefix='模型 中文 空格 ') as directory:
            external = Path(directory) / '任意目录名'
            original = ModelRepo.local_dir
            try:
                with patch.dict('os.environ', {'MINERU_PORTABLE_ONNX_DIR': str(external)}):
                    module.configure_model_paths()
                self.assertEqual(MINERU_4_MODELS_ONNX.ocr_det.local_path(),
                    external / MINERU_4_MODELS_ONNX.paths['ocr_det'])
                self.assertNotIn(external, MINERU_4_MODELS_ONNX.lock_path().parents)
            finally:
                ModelRepo.local_dir = original

    def test_configure_client_timeout_defaults_to_24_hours(self):
        with patch.dict('os.environ', {}, clear=False):
            os.environ.pop('MINERU_POLL_TIMEOUT', None)
            timeout = module.configure_client_timeout()
            self.assertEqual(timeout, 86400)

    def test_configure_client_timeout_reads_custom_env(self):
        with patch.dict('os.environ', {'MINERU_POLL_TIMEOUT': '14400'}):
            timeout = module.configure_client_timeout()
            self.assertEqual(timeout, 14400)

    def test_configure_client_timeout_enforces_minimum_3600(self):
        with patch.dict('os.environ', {'MINERU_POLL_TIMEOUT': '500'}):
            timeout = module.configure_client_timeout()
            self.assertEqual(timeout, 3600)

    def test_configure_client_timeout_patches_api_client(self):
        fake_api_client = types.ModuleType('mineru.parser.api_client')
        setattr(fake_api_client, '_POLL_MAX_ATTEMPTS', 3600)
        fake_mineru = types.ModuleType('mineru')
        fake_parser = types.ModuleType('mineru.parser')
        with patch.dict(sys.modules, {
            'mineru': fake_mineru,
            'mineru.parser': fake_parser,
            'mineru.parser.api_client': fake_api_client,
        }):
            module.configure_client_timeout(7200)
            self.assertEqual(getattr(fake_api_client, '_POLL_MAX_ATTEMPTS'), 7200)


if __name__ == '__main__':
    unittest.main()
