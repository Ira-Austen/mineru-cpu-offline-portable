import importlib.util
import tempfile
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
