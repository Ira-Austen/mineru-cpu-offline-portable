"""识别前清除页面位图上的红色印章。

补丁挂在解析窗口的整页渲染入口
（``mineru.backend.analysis.pdf.images.load_images_from_pdf_bytes_range``）上：
页面位图一渲染出来就先去掉红章，之后的 Layout、OCR、公式/表格识别、VLM
以及输出里的图片裁切都只看到去章后的位图，印章位置因此恢复成普通文字版面。
只影响识别输入；原始文件、WebUI 预览与输出 PDF 都不改动。

默认参数以 200dpi A4 页面为基准：清除"见方接近或大于 1.2cm、长宽比接近正方形"
的红色区块，红色标题、红色批注这类又长又窄或又窄又高的红字会被保留。
"""
from __future__ import annotations

import functools
import importlib.machinery
import os
import sys
from pathlib import Path

TARGET_MODULE = 'mineru.backend.analysis.pdf.images'
CONSUMER_MODULE = 'mineru.backend.analysis.pdf.window'
ENV_ENABLE = 'MINERU_PORTABLE_SEAL_CLEAN'
STATE_ENV = 'MINERU_PORTABLE_SEAL_STATE'
DEFAULT_STATE_FILE = 'data/seal-clean.txt'

# R 通道比 G/B 高出多少、且 R 至少多大，才判定为红墨
DEFAULT_DELTA = 35
DEFAULT_MIN_RED = 85
# 降采样网格边长（像素）与小网格上的膨胀半径（单元），等效合并间距约 50 像素
DEFAULT_SCALE = 5
DEFAULT_MERGE_RADIUS = 10
# 印章判定的几何门槛：最小边长相对页面短边、长宽比区间、墨迹面积相对页面面积
DEFAULT_MIN_SIDE_RATIO = 0.06
DEFAULT_ASPECT = (0.6, 1.7)
DEFAULT_MIN_AREA_RATIO = 0.0008
# 红通道不低于该阈值的判定像素直接还原为白纸，低于则保留红通道灰度（章下黑字）
DEFAULT_WHITE_GATE = 200

_INSTALLED = False
_RUNTIME = None
_WARNED = set()
_STATE_CACHE = None


def _runtime():
    """惰性加载 numpy 与 Pillow，避免不解析文档的进程为补丁付出导入成本。"""
    global _RUNTIME
    if _RUNTIME is None:
        import numpy
        from PIL import Image

        _RUNTIME = (numpy, Image)
    return _RUNTIME


def _warn(message):
    """同一类告警每个进程只输出一次，避免整份文档反复刷屏。"""
    if message in _WARNED:
        return
    _WARNED.add(message)
    try:
        from loguru import logger

        logger.warning(message)
    except Exception:
        print(message, file=sys.stderr, flush=True)


def state_path():
    """开关状态文件位置：环境变量优先，缺省为运行目录下的 data/seal-clean.txt。"""
    raw = os.environ.get(STATE_ENV, '').strip()
    return Path(raw).expanduser() if raw else Path.cwd() / DEFAULT_STATE_FILE


def read_state():
    """读取 WebUI 写入的开关状态；未设置或不可读时返回 None。"""
    global _STATE_CACHE
    path = state_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        _STATE_CACHE = None
        return None
    key = (str(path), stamp)
    if _STATE_CACHE is not None and _STATE_CACHE[0] == key:
        return _STATE_CACHE[1]
    try:
        text = path.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    value = text == '1' if text in ('0', '1') else None
    _STATE_CACHE = (key, value)
    return value


def write_state(value):
    """写入开关状态，供 WebUI 与设置页共用；返回是否写入成功。"""
    global _STATE_CACHE
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('1' if value else '0', encoding='utf-8')
    except OSError as exc:
        _warn(f'去章开关状态写入失败：{exc}')
        return False
    _STATE_CACHE = None
    return True


def enabled():
    """本次渲染是否去章：界面状态文件优先，其次环境变量，默认开启。"""
    state = read_state()
    if state is not None:
        return state
    return os.environ.get(ENV_ENABLE, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def red_mask(array, delta=DEFAULT_DELTA, min_red=DEFAULT_MIN_RED):
    """返回红墨掩码：R 明显高于 G/B 且亮度足够的像素。"""
    numpy, _ = _runtime()
    r = array[..., 0].astype(numpy.int16)
    g = array[..., 1].astype(numpy.int16)
    b = array[..., 2].astype(numpy.int16)
    return (r - numpy.maximum(g, b) > delta) & (r > min_red)


def _row_runs(mask):
    """按行提取游程，返回 (行号, 起点, 终点) 三个数组。"""
    numpy, _ = _runtime()
    ys, starts, ends = [], [], []
    for y in range(mask.shape[0]):
        row = mask[y]
        if not row.any():
            continue
        diff = numpy.diff(numpy.concatenate(([0], row.view(numpy.int8), [0])))
        ys.append(numpy.full(int((diff == 1).sum()), y, dtype=numpy.int32))
        starts.append(numpy.flatnonzero(diff == 1).astype(numpy.int32))
        ends.append(numpy.flatnonzero(diff == -1).astype(numpy.int32))
    if not ys:
        return (numpy.empty(0, numpy.int32),) * 3
    return numpy.concatenate(ys), numpy.concatenate(starts), numpy.concatenate(ends)


def _label_runs(ys, starts, ends):
    """相邻行的重叠游程合并为同一连通域（8 邻域），返回每条游程的域编号。

    空行必须切断连接：只比较行号连续的两行，否则相隔很远的红块会被错误合并。
    """
    numpy, _ = _runtime()
    parent = list(range(int(ys.size)))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    previous, current, previous_y = [], [], -1
    for index in numpy.argsort(ys, kind='stable'):
        y = int(ys[index])
        if y != previous_y:
            previous, current, previous_y = (current if y == previous_y + 1 else []), [], y
        for other in previous:
            if ends[other] >= starts[index] - 1 and starts[other] <= ends[index]:
                left, right = find(int(other)), find(int(index))
                if left != right:
                    parent[max(left, right)] = min(left, right)
        current.append(int(index))
    return numpy.array([find(node) for node in range(int(ys.size))], dtype=numpy.int32)


def _dilate(cells, radius):
    """方形结构元的 8 邻域膨胀；在小网格上执行，用于把印章的圈、字、星合并成一块。"""
    numpy, _ = _runtime()
    grown = cells.copy()
    for _ in range(radius):
        padded = numpy.pad(grown, 1)
        merged = grown.copy()
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                merged |= padded[dy:dy + grown.shape[0], dx:dx + grown.shape[1]]
        grown = merged
    return grown


def seal_removal_mask(
    mask,
    *,
    scale=DEFAULT_SCALE,
    merge_radius=DEFAULT_MERGE_RADIUS,
    min_side_ratio=DEFAULT_MIN_SIDE_RATIO,
    aspect=DEFAULT_ASPECT,
    min_area_ratio=DEFAULT_MIN_AREA_RATIO,
):
    """按几何门槛挑出印章级红块，返回需要清除的像素掩码。

    先降采样再膨胀，把印章的各个独立笔画并成一个区块；红色文字行虽然也会被
    并成一整行，但它的短边远小于门槛，因此不会被判成印章。
    """
    numpy, _ = _runtime()
    height, width = mask.shape
    rows, columns = height // scale, width // scale
    if rows < 1 or columns < 1:
        return numpy.zeros_like(mask)
    cells = mask[:rows * scale, :columns * scale].reshape(rows, scale, columns, scale).any(axis=(1, 3))
    grown = _dilate(cells, merge_radius)
    ys, starts, ends = _row_runs(grown)
    if ys.size == 0:
        return numpy.zeros_like(mask)

    labels = _label_runs(ys, starts, ends)
    boxes = {}
    for index in range(int(ys.size)):
        label = int(labels[index])
        area, y0, x0, y1, x1 = boxes.get(label, (0, ys[index], starts[index], ys[index], ends[index]))
        boxes[label] = (
            area + int(ends[index] - starts[index]),
            min(int(y0), int(ys[index])), min(int(x0), int(starts[index])),
            max(int(y1), int(ys[index])), max(int(x1), int(ends[index])),
        )

    min_side = min(height, width) * min_side_ratio / scale
    verdict = {}
    for label, (_, y0, x0, y1, x1) in boxes.items():
        box_width, box_height = x1 - x0, y1 - y0
        ratio = box_width / max(box_height, 1)
        verdict[label] = min(box_width, box_height) >= min_side and aspect[0] <= ratio <= aspect[1]

    accepted = numpy.zeros_like(grown)
    for index in range(int(ys.size)):
        if verdict[int(labels[index])]:
            accepted[int(ys[index]), int(starts[index]):int(ends[index])] = True

    upsampled = numpy.repeat(numpy.repeat(accepted, scale, axis=0), scale, axis=1)
    removal = mask.copy()
    removal[:rows * scale, :columns * scale] &= upsampled
    removal[rows * scale:, :] = False
    removal[:, columns * scale:] = False
    if removal.sum() < height * width * min_area_ratio:
        return numpy.zeros_like(mask)
    return removal


def clean_array(array, removal, white_gate=DEFAULT_WHITE_GATE):
    """清除判定像素：红通道足够亮时还原为白纸，否则保留红通道灰度以保住章下黑字。"""
    numpy, _ = _runtime()
    cleaned = array.copy()
    channel = array[..., 0][removal].astype(numpy.int16)
    value = numpy.where(channel >= white_gate, 255, channel).astype(numpy.uint8)
    cleaned[removal] = numpy.stack([value, value, value], axis=-1)
    return cleaned


def clean_page_image(image):
    """返回去章后的新页图；没有发现印章时原样返回传入对象。"""
    numpy, pillow = _runtime()
    if getattr(image, 'mode', None) not in ('RGB', 'RGBA'):
        image = image.convert('RGB')
    array = numpy.asarray(image)
    if array.ndim != 3 or array.shape[2] < 3:
        return image
    removal = seal_removal_mask(red_mask(array))
    if not removal.any():
        return image
    return pillow.fromarray(clean_array(array, removal))


def clean_image_dicts(images):
    """就地去章一份渲染结果；单页失败只记录并保留原图，不影响整篇解析。"""
    if not enabled():
        return images
    for item in images or []:
        if not isinstance(item, dict):
            continue
        image = item.get('img_pil')
        if image is None:
            continue
        try:
            cleaned = clean_page_image(image)
        except Exception as exc:  # 去章只是增强，任何异常都不能中断解析
            _warn(f'页图去章失败，保留原始页图：{type(exc).__name__}: {exc}')
            continue
        if cleaned is not image:
            item['img_pil'] = cleaned
            try:
                image.close()
            except Exception:
                pass
    return images


def _patch_module(module):
    """替换模块里的整页渲染入口；重复调用不会重复包装。"""
    original = getattr(module, 'load_images_from_pdf_bytes_range', None)
    if original is None or getattr(original, '_mineru_portable_seal_clean', False):
        return False

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        return clean_image_dicts(original(*args, **kwargs))

    wrapper._mineru_portable_seal_clean = True
    module.load_images_from_pdf_bytes_range = wrapper
    consumer = sys.modules.get(CONSUMER_MODULE)
    if consumer is not None and getattr(consumer, 'load_images_from_pdf_bytes_range', None) is original:
        consumer.load_images_from_pdf_bytes_range = wrapper
    return True


# 同一目标模块可以登记多个补丁（去章开关与旧内核垫片都包装官方构建入口）。
# 因此按模块名共用唯一导入钩子并依次执行；若各自插入一个 finder，后注册的
# 那个会排在最前面抢走 loader，先登记的补丁从此永不执行。
_PATCHERS: dict[str, list] = {}
_FINDERS: dict[str, '_TargetFinder'] = {}


def apply_patchers(module_name, module):
    """依次执行该模块登记的全部补丁；单个失败只记录，不影响其它补丁与模块导入。"""
    applied = False
    for patcher in list(_PATCHERS.get(module_name, ())):
        try:
            applied = bool(patcher(module)) or applied
        except Exception as exc:  # 补丁失败不能让官方模块导入失败
            _warn(f'补丁挂载失败（{module_name}）：{type(exc).__name__}: {exc}')
    return applied


class PatchLoader:
    """在目标模块执行完成后应用已登记的补丁，避免无关进程提前导入重型依赖。"""

    def __init__(self, inner, module_name):
        self._inner = inner
        self._module_name = module_name

    def create_module(self, spec):
        create = getattr(self._inner, 'create_module', None)
        return create(spec) if create is not None else None

    def exec_module(self, module):
        self._inner.exec_module(module)
        apply_patchers(self._module_name, module)


class _TargetFinder:
    """只在指定模块真正被导入时才介入的元路径查找器。"""

    def __init__(self, module_name):
        self._module_name = module_name

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self._module_name:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = PatchLoader(spec.loader, self._module_name)
        return spec


def install_import_hook(module_name, patcher):
    """登记补丁：模块已导入则立即执行，否则挂上该模块唯一的导入钩子。"""
    patchers = _PATCHERS.setdefault(module_name, [])
    if patcher not in patchers:
        patchers.append(patcher)
    module = sys.modules.get(module_name)
    if module is not None:
        return bool(apply_patchers(module_name, module))
    finder = _FINDERS.get(module_name)
    if finder is None or finder not in sys.meta_path:
        finder = _TargetFinder(module_name)
        _FINDERS[module_name] = finder
        sys.meta_path.insert(0, finder)
    return True


def install():
    """安装去章补丁；界面可在运行期改开关，因此始终挂载、按状态动态判断。"""
    global _INSTALLED
    if _INSTALLED:
        return False
    _INSTALLED = True
    install_import_hook(TARGET_MODULE, _patch_module)
    return True


__all__ = [
    'clean_array',
    'clean_image_dicts',
    'clean_page_image',
    'enabled',
    'install',
    'install_import_hook',
    'read_state',
    'red_mask',
    'seal_removal_mask',
    'state_path',
    'write_state',
]
