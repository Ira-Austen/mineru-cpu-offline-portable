"""去章补丁的单元测试：几何判定、章下文字保留、补丁安装与失败回退。"""
import importlib
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import numpy
    from PIL import Image, ImageDraw
except ImportError:  # 便携包一定带 numpy 与 Pillow；源码目录裸跑时跳过
    numpy = None

source = Path(__file__).resolve().parents[1] / 'app' / 'packages' / 'mineru_seal_clean.py'
spec = importlib.util.spec_from_file_location('mineru_seal_clean', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PAGE = (900, 1200)
SEAL = (450, 780, 120)
HEADING_BAND = (slice(120, 180), slice(200, 680))
SEAL_BOX = (slice(SEAL[1] - SEAL[2], SEAL[1] + SEAL[2]), slice(SEAL[0] - SEAL[2], SEAL[0] + SEAL[2]))


def synthetic_page(seal_alpha):
    """合成一页：红色标题行 + 黑色正文 + 可选的半透明红章（覆盖正文）。"""
    base = Image.new('RGB', PAGE, (255, 255, 255))
    draw = ImageDraw.Draw(base)
    for column in range(4):
        x = 200 + column * 120
        draw.rectangle([x, 120, x + 80, 180], fill=(212, 32, 38))
    for line in range(6):
        for column in range(10):
            x, y = 80 + column * 70, 600 + line * 46
            draw.rectangle([x, y, x + 30, y + 26], fill=(0, 0, 0))
    if seal_alpha is None:
        return numpy.asarray(base), numpy.zeros((PAGE[1], PAGE[0]), bool)

    shape = Image.new('L', PAGE, 0)
    painter = ImageDraw.Draw(shape)
    cx, cy, radius = SEAL
    painter.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=255, width=10)
    for angle in range(0, 360, 30):
        radians = numpy.deg2rad(angle)
        painter.line([cx + 50 * numpy.cos(radians), cy + 50 * numpy.sin(radians),
                      cx + 90 * numpy.cos(radians), cy + 90 * numpy.sin(radians)], fill=255, width=8)
    ink = numpy.asarray(shape) > 0
    ratio = (numpy.asarray(shape).astype('float32') / 255.0) * (seal_alpha / 255.0)
    blended = (numpy.asarray(base).astype('float32') * (1 - ratio[..., None])
               + numpy.array([228, 26, 34], 'float32') * ratio[..., None])
    return numpy.clip(blended, 0, 255).astype('uint8'), ink


@unittest.skipIf(numpy is None, '需要 numpy 与 Pillow')
class SealCleanTests(unittest.TestCase):
    def setUp(self):
        module._INSTALLED = False
        module._WARNED.clear()
        self.meta_path = list(sys.meta_path)

    def tearDown(self):
        sys.meta_path[:] = self.meta_path

    def test_seal_is_removed_and_red_heading_is_kept(self):
        sealed, ink = synthetic_page(170)
        removal = module.seal_removal_mask(module.red_mask(sealed))
        self.assertEqual(int(removal[HEADING_BAND].sum()), 0)
        self.assertGreaterEqual((removal & ink).sum() / ink.sum(), 0.9)

    def test_text_under_seal_survives(self):
        reference, _ = synthetic_page(None)
        sealed, ink = synthetic_page(170)
        cleaned = module.clean_array(sealed, module.seal_removal_mask(module.red_mask(sealed)))
        reference_dark = reference[SEAL_BOX].mean(axis=-1) < 128
        kept = (reference_dark & (cleaned[SEAL_BOX].mean(axis=-1) < 160)).sum() / reference_dark.sum()
        self.assertGreaterEqual(kept, 0.95)
        residual = module.red_mask(cleaned, delta=25, min_red=70)[SEAL_BOX].sum()
        self.assertLessEqual(int(residual), ink.sum() * 0.05)

    def test_cleaning_keeps_pixels_outside_removal(self):
        sealed, _ = synthetic_page(170)
        removal = module.seal_removal_mask(module.red_mask(sealed))
        self.assertTrue(numpy.array_equal(module.clean_array(sealed, removal)[~removal], sealed[~removal]))

    def test_blank_lines_do_not_merge_components(self):
        separated = numpy.zeros((260, 200), bool)
        separated[40:100, 40:100] = True
        separated[150:210, 40:100] = True
        ys, starts, ends = module._row_runs(separated)
        self.assertEqual(len(set(module._label_runs(ys, starts, ends).tolist())), 2)

        touching = numpy.zeros((260, 200), bool)
        touching[40:100, 40:100] = True
        touching[100:160, 40:100] = True
        ys, starts, ends = module._row_runs(touching)
        self.assertEqual(len(set(module._label_runs(ys, starts, ends).tolist())), 1)

    def test_page_without_red_returns_same_image(self):
        image = Image.new('RGB', (200, 200), (255, 255, 255))
        self.assertIs(module.clean_page_image(image), image)

    def test_rendered_page_is_cleaned_in_place(self):
        sealed, ink = synthetic_page(170)
        original = Image.fromarray(sealed)
        items = [{'scale': 1.0, 'img_pil': original}]
        module.clean_image_dicts(items)
        self.assertIsNot(items[0]['img_pil'], original)
        residual = module.red_mask(numpy.asarray(items[0]['img_pil']))
        self.assertLessEqual(int(residual[SEAL_BOX].sum()), ink[SEAL_BOX].sum() * 0.05)
        self.assertTrue(residual[HEADING_BAND].any())

    def test_every_page_of_a_multi_page_render_is_cleaned(self):
        """一次渲染返回多页（多页 PDF 的页窗口）时逐页去章，无章页原样保留。"""
        sealed, ink = synthetic_page(170)
        sealed_page = Image.fromarray(sealed)
        plain_page = Image.fromarray(synthetic_page(None)[0])
        items = [{'scale': 1.0, 'img_pil': sealed_page}, {'scale': 1.0, 'img_pil': plain_page}]
        module.clean_image_dicts(items)
        self.assertIsNot(items[0]['img_pil'], sealed_page)
        self.assertIs(items[1]['img_pil'], plain_page)
        residual = module.red_mask(numpy.asarray(items[0]['img_pil']))
        self.assertLessEqual(int(residual[SEAL_BOX].sum()), ink[SEAL_BOX].sum() * 0.05)
        # 无章页的红标题必须原样保留
        self.assertGreater(int(numpy.asarray(items[1]['img_pil'])[HEADING_BAND][..., 0].max()), 180)

    def test_cleaning_failure_keeps_original_page(self):
        original = Image.fromarray(synthetic_page(170)[0])
        with patch.object(module, 'clean_page_image', side_effect=RuntimeError('boom')):
            items = [{'scale': 1.0, 'img_pil': original}]
            module.clean_image_dicts(items)
        self.assertIs(items[0]['img_pil'], original)

    def test_environment_switch_is_read_at_clean_time(self):
        """补丁始终挂载（界面可随时改开关），是否清洗由每次调用时的开关决定。"""
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / '未设置-seal.txt')
            with patch.dict('os.environ', {module.ENV_ENABLE: '0', module.STATE_ENV: missing}):
                module._STATE_CACHE = None
                self.assertFalse(module.enabled())
            with patch.dict('os.environ', {module.ENV_ENABLE: '1', module.STATE_ENV: missing}):
                module._STATE_CACHE = None
                self.assertTrue(module.enabled())

    def test_state_file_overrides_environment(self):
        with tempfile.TemporaryDirectory(prefix='状态 目录 ') as directory:
            state_file = Path(directory) / 'seal-clean.txt'
            with patch.dict('os.environ', {module.ENV_ENABLE: '0', module.STATE_ENV: str(state_file)}):
                module._STATE_CACHE = None
                self.assertIsNone(module.read_state())
                self.assertFalse(module.enabled())
                self.assertTrue(module.write_state(True))
                self.assertTrue(module.read_state())
                self.assertTrue(module.enabled())
                state_file.write_text('0', encoding='utf-8')
                module._STATE_CACHE = None
                self.assertFalse(module.read_state())
                self.assertFalse(module.enabled())

    def test_patched_render_entry_cleans_returned_pages(self):
        sealed, ink = synthetic_page(170)
        images = types.ModuleType('mineru.backend.analysis.pdf.images')
        images.load_images_from_pdf_bytes_range = lambda **kwargs: [{'scale': 1.0, 'img_pil': Image.fromarray(sealed)}]
        with patch.dict(sys.modules, {'mineru.backend.analysis.pdf.images': images}):
            module._INSTALLED = False
            self.assertTrue(module.install())
            result = images.load_images_from_pdf_bytes_range()
            wrapper = images.load_images_from_pdf_bytes_range
            self.assertFalse(module._patch_module(images))
            self.assertIs(images.load_images_from_pdf_bytes_range, wrapper)
        residual = module.red_mask(numpy.asarray(result[0]['img_pil']))
        self.assertLessEqual(int(residual[SEAL_BOX].sum()), ink[SEAL_BOX].sum() * 0.05)

    def test_consumer_module_is_rebound(self):
        images = types.ModuleType('mineru.backend.analysis.pdf.images')

        def render(*args, **kwargs):
            return []

        images.load_images_from_pdf_bytes_range = render
        window = types.ModuleType('mineru.backend.analysis.pdf.window')
        window.load_images_from_pdf_bytes_range = render
        with patch.dict(sys.modules, {'mineru.backend.analysis.pdf.images': images,
                                      'mineru.backend.analysis.pdf.window': window}):
            module._INSTALLED = False
            self.assertTrue(module.install())
            self.assertTrue(getattr(images.load_images_from_pdf_bytes_range, '_mineru_portable_seal_clean', False))
            self.assertIs(window.load_images_from_pdf_bytes_range, images.load_images_from_pdf_bytes_range)

    def test_import_hook_patches_module_loaded_later(self):
        with tempfile.TemporaryDirectory(prefix='去章 补丁 ') as directory:
            root = Path(directory)
            target = root / 'mineru' / 'backend' / 'analysis' / 'pdf'
            for package in (root / 'mineru', root / 'mineru' / 'backend',
                            root / 'mineru' / 'backend' / 'analysis', target):
                package.mkdir(parents=True, exist_ok=True)
                (package / '__init__.py').write_text('', encoding='utf-8')
            (target / 'images.py').write_text(
                'def load_images_from_pdf_bytes_range(*args, **kwargs):\n    return []\n', encoding='utf-8')
            saved = {name: value for name, value in sys.modules.items()
                     if name == 'mineru' or name.startswith('mineru.')}
            for name in saved:
                del sys.modules[name]
            sys.path.insert(0, str(root))
            importlib.invalidate_caches()
            try:
                module._INSTALLED = False
                self.assertTrue(module.install())
                imported = importlib.import_module('mineru.backend.analysis.pdf.images')
                self.assertTrue(getattr(imported.load_images_from_pdf_bytes_range,
                                        '_mineru_portable_seal_clean', False))
            finally:
                sys.path.remove(str(root))
                importlib.invalidate_caches()
                for name in [name for name in sys.modules if name == 'mineru' or name.startswith('mineru.')]:
                    del sys.modules[name]
                sys.modules.update(saved)

    def test_multiple_patchers_on_one_module_all_run(self):
        """同一模块登记多个补丁时必须全部执行。

        回归：早期实现给每个补丁各插一个 finder，后注册的排在 meta_path 最前面
        抢走 loader，先登记的补丁从此静默失效——CI 上去章开关正是这样丢的。
        """
        module._PATCHERS.pop('patchpkg', None)
        module._FINDERS.pop('patchpkg', None)
        seen = []
        with tempfile.TemporaryDirectory(prefix='多补丁 ') as directory:
            root = Path(directory)
            package = root / 'patchpkg'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('value = 0\n', encoding='utf-8')
            sys.path.insert(0, str(root))
            importlib.invalidate_caches()
            try:
                self.assertTrue(module.install_import_hook('patchpkg', lambda mod: seen.append('first') or True))
                self.assertTrue(module.install_import_hook('patchpkg', lambda mod: seen.append('second') or True))
                importlib.import_module('patchpkg')
                imported = sys.modules['patchpkg']
            finally:
                sys.path.remove(str(root))
                importlib.invalidate_caches()
                sys.modules.pop('patchpkg', None)
                module._PATCHERS.pop('patchpkg', None)
                module._FINDERS.pop('patchpkg', None)
        self.assertEqual(seen, ['first', 'second'])
        self.assertEqual(imported.value, 0)


if __name__ == '__main__':
    unittest.main()
