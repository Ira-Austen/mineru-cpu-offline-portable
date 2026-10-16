"""离线便携包只允许 Python 访问本机回环地址。"""
import ipaddress
import os
import socket
from pathlib import Path

_dll_handles = []
if os.name == 'nt':
    _root = Path(__file__).resolve().parent.parent
    for _path in (_root / 'lib', _root / 'runtime' / 'python'):
        if _path.is_dir():
            _dll_handles.append(os.add_dll_directory(str(_path)))

# 延长 WebUI 客户端轮询等待 API 任务的最大超时时间（默认 24 小时），防止 20+ 页 PDF 处理中断
try:
    _raw_timeout = os.environ.get('MINERU_POLL_TIMEOUT', '86400')
    _poll_timeout = max(int(_raw_timeout), 3600)
    import mineru.parser.api_client as _api_client
    _api_client._POLL_MAX_ATTEMPTS = _poll_timeout
except Exception:
    pass

# 识别前清除页面位图上的红色印章（可用 MINERU_PORTABLE_SEAL_CLEAN=0 关闭）
try:
    import mineru_seal_clean

    mineru_seal_clean.install()
except Exception:
    pass

# 官方 WebUI 里的"识别前清除红色印章"开关，只作用于 WebUI 进程
try:
    import mineru_seal_clean_ui

    mineru_seal_clean_ui.install()
except Exception:
    pass

# 官方 WebUI 的旧内核兼容垫片（Chromium 89~97 有效；更低版本请用 7861 兼容页）
try:
    import mineru_webui_compat

    mineru_webui_compat.install()
except Exception:
    pass

if os.environ.get('MINERU_PORTABLE_OFFLINE') == '1':
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex
    _getaddrinfo = socket.getaddrinfo

    def _local(host):
        if host in ('localhost', b'localhost'):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _check(address):
        if isinstance(address, tuple) and not _local(address[0]):
            raise OSError('离线模式禁止访问外部地址')

    def _offline_connect(self, address):
        _check(address)
        return _connect(self, address)

    def _offline_connect_ex(self, address):
        _check(address)
        return _connect_ex(self, address)

    def _offline_getaddrinfo(host, *args, **kwargs):
        if host is not None and not _local(host):
            raise OSError('离线模式禁止外部 DNS 查询')
        return _getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = _offline_connect
    socket.socket.connect_ex = _offline_connect_ex
    socket.getaddrinfo = _offline_getaddrinfo
