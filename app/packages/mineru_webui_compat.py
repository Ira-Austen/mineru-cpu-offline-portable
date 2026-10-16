"""为官方 WebUI 注入旧内核兼容垫片。

Gradio 6 的前端产物在 Chromium 89~97 区间会因为缺少部分运行时 API
（``structuredClone``、``Object.hasOwn``、``Array.prototype.at``、
``String.prototype.replaceAll`` 等）而局部报错，表现为界面空白或只显示一部分。
这里在官方脚本之前插入一段 ES5 polyfill，把这段区间救回来。

Chromium 80~84 仍不可用：前端产物里有 ``??=`` 这类语法，加载即报错，polyfill
无法修复，这类内核请使用 127.0.0.1:7861 的兼容页。
"""
from __future__ import annotations

import functools
import sys

import mineru_seal_clean as seal

TARGET_MODULE = 'mineru.kit.gradio.app'
MARKER = '/* MinerU legacy-browser compatibility shims */'

POLYFILL_JS = MARKER + r"""
(function (global) {
  'use strict';
  if (typeof global.structuredClone !== 'function') {
    global.structuredClone = function (value) {
      if (value === undefined) { return undefined; }
      return JSON.parse(JSON.stringify(value));
    };
  }
  if (typeof Object.hasOwn !== 'function') {
    Object.hasOwn = function (object, key) { return Object.prototype.hasOwnProperty.call(object, key); };
  }
  if (typeof Array.prototype.at !== 'function') {
    Array.prototype.at = function (index) {
      var length = this.length >>> 0;
      var position = Math.trunc(Number(index)) || 0;
      if (position < 0) { position += length; }
      return position >= 0 && position < length ? this[position] : undefined;
    };
  }
  if (typeof String.prototype.at !== 'function') {
    String.prototype.at = function (index) {
      var value = String(this);
      var position = Math.trunc(Number(index)) || 0;
      if (position < 0) { position += value.length; }
      return position >= 0 && position < value.length ? value.charAt(position) : undefined;
    };
  }
  if (typeof String.prototype.replaceAll !== 'function') {
    String.prototype.replaceAll = function (search, replacement) {
      if (search instanceof RegExp) {
        if (!search.global) { throw new TypeError('replaceAll requires a global regular expression'); }
        return this.replace(search, replacement);
      }
      var needle = String(search);
      if (typeof replacement === 'function') {
        return this.split(needle).join(replacement(needle));
      }
      return this.split(needle).join(String(replacement));
    };
  }
  if (typeof Promise.any !== 'function') {
    Promise.any = function (iterable) {
      return new Promise(function (resolve, reject) {
        var items = [];
        var settled = 0;
        try {
          Array.prototype.forEach.call(iterable, function (item) { items.push(item); });
        } catch (error) {
          reject(error);
          return;
        }
        if (!items.length) {
          reject(new Error('All promises were rejected'));
          return;
        }
        items.forEach(function (item) {
          Promise.resolve(item).then(resolve, function () {
            settled += 1;
            if (settled === items.length) { reject(new Error('All promises were rejected')); }
          });
        });
      });
    };
  }
})(typeof globalThis !== 'undefined' ? globalThis : window);
"""

_WARNED = set()


def _warn(message):
    if message in _WARNED:
        return
    _WARNED.add(message)
    try:
        from loguru import logger

        logger.warning(message)
    except Exception:
        print(message, file=sys.stderr, flush=True)


def inject(demo):
    """把垫片放到官方脚本之前；重复调用不会重复注入。"""
    launch_kwargs = getattr(demo, '_mineru_kit_launch_kwargs', None)
    if not isinstance(launch_kwargs, dict):
        return False
    script = launch_kwargs.get('js') or ''
    if MARKER in script:
        return False
    launch_kwargs['js'] = POLYFILL_JS + '\n' + script
    return True


def _patch(module):
    """包装官方界面构建入口，构建完成后注入垫片；重复调用不会重复包装。"""
    original = getattr(module, 'build_gradio_app', None)
    if original is None or getattr(original, '_mineru_portable_legacy_shims', False):
        return False

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        demo = original(*args, **kwargs)
        try:
            inject(demo)
        except Exception as exc:  # 垫片只是兼容增强，不能影响界面启动
            _warn(f'WebUI 兼容垫片注入失败：{type(exc).__name__}: {exc}')
        return demo

    wrapper._mineru_portable_legacy_shims = True
    module.build_gradio_app = wrapper
    return True


def install():
    """安装兼容垫片；重复调用无副作用。"""
    return seal.install_import_hook(TARGET_MODULE, _patch)


__all__ = ['POLYFILL_JS', 'inject', 'install']
