"""在官方 MinerU WebUI 里加一个"识别前清除红色印章"开关。

官方界面由 gradio.Blocks 构建，构建结束布局就固定了，因此补丁在
``build_gradio_app`` 执行期间临时包裹布局容器的 ``__exit__``，把一个原生
Checkbox 插进控制列末尾；勾选状态写入状态文件，解析进程每次渲染页图时读取，
因此改完立即生效、无需重启。

补丁只包装官方函数、不改写包内文件；任何一步失败都退回"不改界面"并留下日志，
不影响 WebUI 正常启动。
"""
from __future__ import annotations

import contextlib
import functools
import sys

import mineru_seal_clean as seal

TARGET_MODULE = 'mineru.kit.gradio.app'
CONTROL_COLUMN_CLASS = 'mineru-control-column'
CHECKBOX_CLASS = 'mineru-portable-seal-clean'
LABEL = '识别前清除红色印章'
INFO = '解析前抹掉红色印章，避免印章位置在结果里变成图片；红色标题和红色批注保留。'

_INSTALLED = False
_WARNED = set()


def _warn(message):
    """同一类告警每个进程只输出一次。"""
    if message in _WARNED:
        return
    _WARNED.add(message)
    try:
        from loguru import logger

        logger.warning(message)
    except Exception:
        print(message, file=sys.stderr, flush=True)


def initial_state():
    """复选框初值：状态文件优先，未设置时与解析默认值（开启）一致。"""
    state = seal.read_state()
    return True if state is None else state


def notice(value):
    """勾选状态提示，同时作为事件输出以便界面即时反馈。"""
    return f'**{LABEL}：{"已开启" if value else "已关闭"}**（对后续解析立即生效，无需重启）'


def apply_state(value):
    """勾选变化时写入状态文件，解析进程下一步就会读到。"""
    enabled = bool(value)
    if not seal.write_state(enabled):
        _warn('去章开关状态写入失败，本次勾选可能未生效')
    return notice(enabled)


def add_controls():
    """在当前渲染上下文（控制列或根容器）里创建复选框与提示行。

    调用点位于官方布局容器的 ``__exit__`` 之前，此时渲染上下文仍是该容器，
    因此新建的组件会挂进这个容器并完成注册。
    """
    import gradio as gr

    checkbox = gr.Checkbox(
        value=initial_state(),
        label=LABEL,
        info=INFO,
        interactive=True,
        elem_classes=[CHECKBOX_CLASS],
    )
    notice_line = gr.Markdown(notice(initial_state()), elem_classes=[CHECKBOX_CLASS + '-notice'])
    checkbox.change(fn=apply_state, inputs=[checkbox], outputs=[notice_line])
    return checkbox


@contextlib.contextmanager
def _layout_hooks():
    """在官方构建窗口内临时包裹布局 __exit__，构建结束立即恢复原状。"""
    try:
        import gradio as gr
    except Exception as exc:  # 没有 gradio 的进程不需要界面补丁，直接跳过
        _warn(f'WebUI 去章开关未安装（无法导入 gradio）：{type(exc).__name__}: {exc}')
        yield
        return

    column_exit = gr.Column.__exit__
    blocks_exit = gr.Blocks.__exit__
    column_owned = '__exit__' in vars(gr.Column)
    blocks_owned = '__exit__' in vars(gr.Blocks)
    state = {'added': False}

    def insert():
        state['added'] = True
        try:
            add_controls()
            return True
        except Exception as exc:
            _warn(f'WebUI 去章开关插入失败：{type(exc).__name__}: {exc}')
            return False

    def patched_column_exit(self, *args, **kwargs):
        try:
            if not state['added'] and CONTROL_COLUMN_CLASS in (getattr(self, 'elem_classes', None) or []):
                insert()
        finally:
            return column_exit(self, *args, **kwargs)

    def patched_blocks_exit(self, *args, **kwargs):
        try:
            # 兜底：没有找到控制列时挂在根容器末尾，保证开关一定可见。
            if not state['added']:
                insert()
        finally:
            return blocks_exit(self, *args, **kwargs)

    gr.Column.__exit__ = patched_column_exit
    gr.Blocks.__exit__ = patched_blocks_exit
    try:
        yield
    finally:
        _restore(gr.Column, column_exit, column_owned)
        _restore(gr.Blocks, blocks_exit, blocks_owned)


def _restore(cls, original, was_owned):
    if was_owned:
        cls.__exit__ = original
    else:
        with contextlib.suppress(AttributeError):
            del cls.__exit__


def _patch_build_app(module):
    """包装官方界面构建入口；重复调用不会重复包装。"""
    original = getattr(module, 'build_gradio_app', None)
    if original is None or getattr(original, '_mineru_portable_seal_ui', False):
        return False

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        with _layout_hooks():
            return original(*args, **kwargs)

    wrapper._mineru_portable_seal_ui = True
    module.build_gradio_app = wrapper
    return True


def install():
    """安装 WebUI 开关补丁；开关状态可在运行期改变，因此始终挂载。"""
    global _INSTALLED
    if _INSTALLED:
        return False
    _INSTALLED = True
    seal.install_import_hook(TARGET_MODULE, _patch_build_app)
    return True


__all__ = ['add_controls', 'apply_state', 'initial_state', 'install', 'notice']
