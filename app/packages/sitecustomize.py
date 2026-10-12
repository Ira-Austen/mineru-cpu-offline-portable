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
