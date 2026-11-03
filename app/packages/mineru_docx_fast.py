"""加速本地 DOCX 导出：复用独占副本、缓存公式与图片、避免媒体二次压缩。

MinerU 的本地 DOCX 导出最后都落在固定版本的 docvortex DOCX 渲染器上
（``mineru.render.docx.render_docx`` → ``docvortex.render.docx.render_docx``）。
对着 docvortex 0.5.16 与 python-docx 1.2.0 的源码逐条核对，长文档导出慢在四处
纯浪费上，补丁只针对这四处：

1. ``build_render_plan`` 会先对整份 Middle JSON 做一次 ``model_copy(deep=True)``，
   只为合并跨页续文做准备。docvortex 自己的顶层 API 用 ``owned_render_document``
   声明"这份文档归本次渲染独占"从而跳过拷贝，而 mineru 的 render 直接调用
   ``docvortex.render.docx.render_docx``，没有这个声明，于是每次导出都白付一次
   整树深拷贝。
2. ``latex_to_omml`` 对每条公式重跑一遍纯 Python 的 LaTeX→MathML→OMML，没有任何
   缓存；同一段 LaTeX（变量、编号、常见符号）在正文、表格、目录里出现几次就算几次。
3. ``prepare_image_bytes`` 对每一次图片引用都重新做 PIL 全量解码
   （``image.load()``）；同一张图出现几次就解码几次。
4. python-docx 保存时对整个包用 zlib 默认级别压缩，已经压过的 JPEG/PNG 被再压一遍，
   只花 CPU 换不到体积。
5. ``add_paragraph(style=...)`` 每次都线性扫描样式表找默认样式
   （``CT_Styles.default_for``，按样式类型遍历全部 style 元素）；580 页测试文档里
   这一步单独占掉约四分之一的渲染时间，而样式表在渲染期间不会再变化。

五项都不改变渲染语义：公式缓存命中时交出全新解析的 OMML 节点（调用方会就地把
节点插进文档并改属性），图片缓存共享不可变的 ``PreparedImage``，DOCX 里仍是原始
图片字节，样式缓存只复用同一个查找结果，正文、表格与公式的排版结果不变。

前提与代价：独占副本复用会让渲染器就地合并跨页续接列表，因此调用方不能在导出后
继续复用同一份 MiddleJson 对象。便携包的两条导出路径（官方 WebUI 的
``render_download`` 与兼容页的 ``export_document``）都是每次导出现建 ParseResult，
因此安全；其它调用方可用 ``MINERU_PORTABLE_DOCX_OWNED=0`` 关掉这一项。

开关与上限：``MINERU_PORTABLE_DOCX_FAST=0`` 关闭全部补丁；公式、图片与样式查找缓存
的条目上限用 ``MINERU_PORTABLE_DOCX_FORMULA_CACHE``、``MINERU_PORTABLE_DOCX_IMAGE_CACHE``、
``MINERU_PORTABLE_DOCX_STYLE_CACHE``；XML 压缩级别用 ``MINERU_PORTABLE_DOCX_ZIP_LEVEL``
（0 为默认，沿用 python-docx 级别）。
"""
from __future__ import annotations

import os
import sys
import threading

import mineru_seal_clean as seal

TARGET_MODULE = 'docvortex.render.docx'
MEDIA_PREFIX = 'word/media/'
#: 补丁标记：同一个可调用对象只包装一次
MARK = '_mineru_portable_docx_fast'

ENV_ENABLE = 'MINERU_PORTABLE_DOCX_FAST'
ENV_OWNED = 'MINERU_PORTABLE_DOCX_OWNED'
ENV_FORMULA_CACHE = 'MINERU_PORTABLE_DOCX_FORMULA_CACHE'
ENV_IMAGE_CACHE = 'MINERU_PORTABLE_DOCX_IMAGE_CACHE'
ENV_STYLE_CACHE = 'MINERU_PORTABLE_DOCX_STYLE_CACHE'
ENV_ZIP_LEVEL = 'MINERU_PORTABLE_DOCX_ZIP_LEVEL'

DEFAULT_FORMULA_CACHE = 512
DEFAULT_IMAGE_CACHE = 64
DEFAULT_STYLE_CACHE = 64
#: 图片本身已压缩，原样存放只省 CPU 不亏体积；XML 仍用 python-docx 的默认级别，
#: 想再换速度可把 ``MINERU_PORTABLE_DOCX_ZIP_LEVEL`` 设为 1（体积会略增）
DEFAULT_ZIP_LEVEL = 0

_WARNED: set[str] = set()
_LOCK = threading.Lock()
_PREPARED = False
_INSTALLED = False
_OWNED_CONTEXT = None


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


def _flag(name, default=True):
    """读取布尔开关；未设置或空值取默认。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ('0', 'false', 'no', 'off')


def _limit(name, default):
    """读取非负整数上限；无法解析时取默认。"""
    try:
        value = int(str(os.environ.get(name, '')).strip())
    except (TypeError, ValueError):
        return default
    return max(0, value)


def enabled():
    """DOCX 导出加速是否启用，默认开启。"""
    return _flag(ENV_ENABLE, True)


def owned_document_enabled():
    """是否复用独占副本以跳过整树深拷贝，默认开启。"""
    return enabled() and _flag(ENV_OWNED, True)


def limit_reached(cache, limit):
    """缓存是否已满：满了整体清空，避免无界增长。"""
    return bool(limit) and len(cache) >= limit


def install_formula_cache():
    """按 (LaTeX, display) 缓存 OMML 节点，命中时交出全新节点。

    调用方会把返回的节点直接插入文档，并就地改属性（居中、剥掉 ``m:oMath``），
    所以缓存里只留序列化后的原始副本，每次命中重新解析成独立节点。
    """
    from lxml import etree

    from docvortex.render._internal.docx import inline, math as docx_math, renderer

    original = docx_math.latex_to_omml
    if getattr(original, MARK, False):
        return False
    cache: dict[tuple[str, bool], bytes] = {}
    limit = _limit(ENV_FORMULA_CACHE, DEFAULT_FORMULA_CACHE)

    def latex_to_omml(latex, *args, **kwargs):
        display = bool(kwargs.get('display', args[0] if args else False))
        serialized = cache.get((latex, display))
        if serialized is not None:
            return etree.fromstring(serialized)
        element = original(latex, *args, **kwargs)
        try:
            serialized = etree.tostring(element)
        except Exception:  # 序列化失败时退回原节点，不影响本次渲染
            return element
        if limit_reached(cache, limit):
            cache.clear()
        cache[(latex, display)] = serialized
        return element

    latex_to_omml.__doc__ = original.__doc__
    setattr(latex_to_omml, MARK, True)
    docx_math.latex_to_omml = latex_to_omml
    # 这两个模块在导入时就把函数绑到了自己的命名空间，必须一并替换
    for module in (inline, renderer):
        module.latex_to_omml = latex_to_omml
    return True


def install_image_cache():
    """按图片内容与声明扩展名缓存解码结果，避免重复 PIL 解码。"""
    from hashlib import sha1

    from docvortex.render._internal.docx import assets

    original = assets.prepare_image_bytes
    if getattr(original, MARK, False):
        return False
    cache: dict[tuple[bytes, str | None], object] = {}
    limit = _limit(ENV_IMAGE_CACHE, DEFAULT_IMAGE_CACHE)

    def prepare_image_bytes(data, *, declared_extension=None):
        key = (sha1(data).digest(), declared_extension)
        prepared = cache.get(key)
        if prepared is None:
            prepared = original(data, declared_extension=declared_extension)
            if limit_reached(cache, limit):
                cache.clear()
            cache[key] = prepared
        return prepared

    prepare_image_bytes.__doc__ = original.__doc__
    setattr(prepare_image_bytes, MARK, True)
    # 同模块内的相对路径与 data URI 分支都通过模块全局调用它
    assets.prepare_image_bytes = prepare_image_bytes
    return True


def install_zip_compression():
    """DOCX 内的图片原样存放，其余成员按较低级别压缩。"""
    import zipfile

    from docx.opc import phys_pkg

    original = phys_pkg._ZipPkgWriter.write
    if getattr(original, MARK, False):
        return False
    level = _limit(ENV_ZIP_LEVEL, DEFAULT_ZIP_LEVEL)

    def write(self, pack_uri, blob):
        member = pack_uri.membername
        if member.startswith(MEDIA_PREFIX):
            self._zipf.writestr(member, blob, compress_type=zipfile.ZIP_STORED)
            return
        if level:
            self._zipf.writestr(member, blob, compresslevel=level)
            return
        original(self, pack_uri, blob)

    setattr(write, MARK, True)
    phys_pkg._ZipPkgWriter.write = write
    return True


def install_style_cache():
    """缓存默认样式解析，省掉每个段落一次的全样式表线性扫描。

    ``add_paragraph(style=...)`` 会带名字去查样式 id，命中默认样式时 python-docx 用
    ``CT_Styles.default_for`` 遍历全部 style 元素；样式表在渲染期间只读，结果可复用。
    缓存按元素身份比对（``Styles`` 包装对象可能每次重建，用其 XML 元素做身份），并在
    新增样式后清空，避免把别的文档的结果串进来。默认样式的 id 是 ``None``，正文段落
    全走这条路径，所以这个答案也一并缓存。
    """
    from docx.oxml import styles as oxml_styles
    from docx.styles import styles as pydocx_styles

    original = oxml_styles.CT_Styles.default_for
    if getattr(original, MARK, False):
        return False
    cache: dict[tuple[int, object], tuple[object, object]] = {}
    limit = _limit(ENV_STYLE_CACHE, DEFAULT_STYLE_CACHE)

    def default_for(self, style_type):
        identity = self
        key = (id(identity), style_type)
        entry = cache.get(key)
        if entry is not None and entry[0] is identity:
            return entry[1]
        value = original(self, style_type)
        if value is not None:
            if limit_reached(cache, limit):
                cache.clear()
            cache[key] = (identity, value)
        return value

    default_for.__doc__ = original.__doc__
    setattr(default_for, MARK, True)
    oxml_styles.CT_Styles.default_for = default_for

    # 按名字查样式 id 走的是 xpath 扫描整张样式表，同样可以按文档复用；
    # Styles 包装对象可能每次重建，所以用它的 XML 元素做稳定身份
    original_by_name = getattr(pydocx_styles.Styles, '_get_style_id_from_name', None)
    if original_by_name is not None and not getattr(original_by_name, MARK, False):
        def _get_style_id_from_name(self, style_name, style_type):
            identity = getattr(self, '_element', self)
            key = (id(identity), style_name, style_type)
            entry = cache.get(key)
            if entry is not None and entry[0] is identity:
                return entry[1]
            value = original_by_name(self, style_name, style_type)
            # 目标样式就是该类型的默认样式时返回 None，这是稳定答案，必须一起缓存：
            # 正文段落全都走这条路径，不缓存等于每个段落都重新扫一遍样式表
            if limit_reached(cache, limit):
                cache.clear()
            cache[key] = (identity, value)
            return value

        _get_style_id_from_name.__doc__ = original_by_name.__doc__
        setattr(_get_style_id_from_name, MARK, True)
        pydocx_styles.Styles._get_style_id_from_name = _get_style_id_from_name

    # 新增样式的路径很少，但一旦发生就必须让缓存失效
    original_add_style = pydocx_styles.Styles.add_style
    if not getattr(original_add_style, MARK, False):
        def add_style(self, *args, **kwargs):
            try:
                return original_add_style(self, *args, **kwargs)
            finally:
                cache.clear()

        add_style.__doc__ = original_add_style.__doc__
        setattr(add_style, MARK, True)
        pydocx_styles.Styles.add_style = add_style
    return True


def _owned_document(middle_json):
    """进入 docvortex 的独占文档上下文，让规划器跳过整树深拷贝。"""
    global _OWNED_CONTEXT
    if _OWNED_CONTEXT is None:
        from docvortex.render._internal.common import context

        _OWNED_CONTEXT = context.owned_render_document
    return _OWNED_CONTEXT(middle_json)


def prepare():
    """首次真正渲染前打上各项缓存与压缩补丁；返回生效项名称，便于诊断。"""
    global _PREPARED
    with _LOCK:
        if _PREPARED:
            return ()
        _PREPARED = True
        applied = []
        for name, installer in (
            ('公式缓存', install_formula_cache),
            ('图片缓存', install_image_cache),
            ('样式缓存', install_style_cache),
            ('包压缩', install_zip_compression),
        ):
            try:
                if installer():
                    applied.append(name)
            except Exception as exc:  # 补丁失败不能影响导出
                _warn(f'DOCX 导出加速补丁未生效（{name}）：{type(exc).__name__}: {exc}')
        return tuple(applied)


def _patch_module(module):
    """包装 docvortex 的 DOCX 门面入口；其余补丁等真正渲染时再打。"""
    if not enabled():
        return False
    original = getattr(module, 'render_docx', None)
    if original is None or getattr(original, MARK, False):
        return False

    def render_docx(middle_json, *, asset_resolver=None):
        prepare()
        if owned_document_enabled():
            try:
                with _owned_document(middle_json):
                    return original(middle_json, asset_resolver=asset_resolver)
            except Exception as exc:  # 上下文不可用时退回官方行为
                _warn(f'DOCX 独占副本复用未生效：{type(exc).__name__}: {exc}')
        return original(middle_json, asset_resolver=asset_resolver)

    render_docx.__doc__ = original.__doc__
    setattr(render_docx, MARK, True)
    module.render_docx = render_docx
    return True


def install():
    """登记惰性补丁：只在 docvortex 的 DOCX 门面被导入时才介入。"""
    global _INSTALLED
    if _INSTALLED:
        return False
    _INSTALLED = True
    if not enabled():
        return False
    return seal.install_import_hook(TARGET_MODULE, _patch_module)


__all__ = [
    'enabled',
    'install',
    'install_formula_cache',
    'install_image_cache',
    'install_style_cache',
    'install_zip_compression',
    'limit_reached',
    'owned_document_enabled',
    'prepare',
]
