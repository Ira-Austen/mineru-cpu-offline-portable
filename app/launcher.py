"""本地设置页和服务管理器；模型与所有可写状态均由解压目录定位。"""
from __future__ import annotations

import argparse
import html
import importlib.metadata
import json
import os
import socket
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / 'settings.json'
sys.path.insert(0, str(ROOT / 'packages'))
CHILDREN = []
LOGS = []
LOCK = threading.Lock()
STATUS = '尚未启动。'
BUSY = False
STOPPING = threading.Event()
PORTS = (30000, 8000, 7860, 7861)
COMPAT_PORT = 7861
# 仅用于本机回环的固定 key：解锁 html/docx/latex 导出，不作为安全边界。
LOCAL_API_KEY = 'mineru-portable-local'
WINDOWS_JOB = None


def create_windows_job():
    """关闭启动窗口时由 Windows 自动终止包内子进程。"""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [('user', ctypes.c_int64), ('job', ctypes.c_int64), ('flags', wintypes.DWORD),
                    ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                    ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000
    if not handle or not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    return api, handle


def read_settings():
    return json.loads(SETTINGS.read_text(encoding='utf-8-sig'))


def seal_state_path():
    """WebUI 与设置页共用的去章开关状态文件。"""
    return ROOT / 'data' / 'seal-clean.txt'


def read_seal_state():
    """读取状态文件；未设置或不可读时返回 None，表示沿用 settings.json。"""
    try:
        return seal_state_path().read_text(encoding='utf-8').strip() != '0'
    except OSError:
        return None


def write_seal_state(value):
    """写入状态文件，让 WebUI 与设置页显示同一状态。"""
    path = seal_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('1' if value else '0', encoding='utf-8')
    except OSError:
        pass


def model_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def check_gguf(path):
    if not path.is_file():
        raise ValueError(f'找不到模型文件：{path}')
    with path.open('rb') as stream:
        if stream.read(4) != b'GGUF':
            raise ValueError(f'不是 GGUF 文件：{path}')


def resolve_models(settings):
    gguf = settings.get('gguf', '').strip()
    mmproj = settings.get('mmproj', '').strip()
    if not gguf or not mmproj:
        raise ValueError('请填写 MinerU2.5-Pro-2605 的主模型和配套 mmproj 完整路径。')
    paths = model_path(gguf), model_path(mmproj)
    for path in paths:
        check_gguf(path)
    return paths


def onnx_directory(settings):
    value = settings.get('onnx_dir', '').strip()
    path = model_path(value) if value else ROOT / 'models' / 'MinerU-4_models_onnx'
    nested = path / 'MinerU-4_models_onnx'
    if not (path / 'Layout').is_dir() and nested.is_dir():
        return nested
    return path


def check_onnx(settings):
    from mineru.model.registry import MINERU_4_MODELS_ONNX
    directory = onnx_directory(settings)
    missing = [rel for rel in MINERU_4_MODELS_ONNX.paths.values()
               if not (directory / rel).is_file() or (directory / rel).stat().st_size == 0]
    if missing:
        raise ValueError(f'ONNX 目录缺少必要文件：{directory}；缺少：{", ".join(missing)}。'
                         '请指定含 Layout、OCR、MFR、Table 的完整模型目录。')
    return directory


def environment(settings):
    env = os.environ.copy()
    # 便携启动不继承宿主的远程模型地址、额外模型配置或 Python 环境。
    for key in list(env):
        if key.startswith(('MINERU_', 'LLAMA_ARG_')) or key in ('PYTHONHOME',):
            env.pop(key)
    data = ROOT / 'data'
    for name in ('logs', 'tmp', 'cache', 'mineru', 'uploads'):
        (data / name).mkdir(parents=True, exist_ok=True)
    env.update({
        'PYTHONPATH': str(ROOT / 'packages'), 'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8', 'PYTHONNOUSERSITE': '1',
        'MINERU_HOME': str(data / 'mineru'),
        'MINERU_MODEL_BASE_DIR': str(data / 'model-state'),
        'MINERU_PORTABLE_ONNX_DIR': str(onnx_directory(settings)),
        'MINERU_POLL_TIMEOUT': str(settings.get('poll_timeout', 86400)),
        'MINERU_PORTABLE_SEAL_CLEAN': '1' if settings.get('seal_clean', True) else '0',
        'MINERU_PORTABLE_SEAL_STATE': str(seal_state_path()),
        'MINERU_API_KEY': LOCAL_API_KEY,
        'MINERU_PORTABLE_API_URL': 'http://127.0.0.1:8000',
        'MINERU_MODEL_SOURCE': 'local', 'MINERU_MODEL_SMALL_BACKEND': 'onnx',
        'MINERU_MODEL_VLM_ENGINE': 'llama-cpp', 'MINERU_TABLE_DEVICE': 'cpu',
        'MINERU_MODEL_VLM_SERVER_URL': 'http://127.0.0.1:30000',
        'MINERU_MODEL_VLM_MODEL': 'MinerU2.5-Pro-2605',
        'MINERU_MODEL_VLM_HTTP_TIMEOUT': '1800',
        'MINERU_MODEL_VLM_MAX_CONCURRENCY': '1',
        'MINERU_PROCESSING_WINDOW_SIZE': '1', 'MINERU_PDF_RENDER_THREADS': '1',
        'MINERU_INTRA_OP_NUM_THREADS': str(settings['threads']),
        'MINERU_INTER_OP_NUM_THREADS': '1', 'OMP_NUM_THREADS': str(settings['threads']),
        'MINERU_PORTABLE_OFFLINE': '1', 'HF_HUB_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HOME': str(data / 'cache' / 'huggingface'),
        'GRADIO_ANALYTICS_ENABLED': 'False', 'GRADIO_TEMP_DIR': str(data / 'tmp'),
        'TMP': str(data / 'tmp'), 'TEMP': str(data / 'tmp'), 'TMPDIR': str(data / 'tmp'),
        'NO_PROXY': 'localhost,127.0.0.1,::1', 'no_proxy': 'localhost,127.0.0.1,::1',
    })
    return env


def command(*args):
    return [sys.executable, '-s', str(ROOT / 'entry.py'), *map(str, args)]


def llama_command(*args):
    suffix = '.exe' if os.name == 'nt' else ''
    binary = ROOT / 'packages' / 'mineru_llama_cpp' / 'bin' / ('llama-server' + suffix)
    return [str(binary), *map(str, args)]


def stop_children():
    global BUSY
    for child in reversed(CHILDREN):
        if child.poll() is None:
            if os.name == 'nt':
                child.terminate()
            else:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    CHILDREN.clear()
    for log in LOGS:
        log.close()
    LOGS.clear()
    BUSY = False


def spawn(name, args, env):
    log = (ROOT / 'data' / 'logs' / f'{name}.log').open('wb')
    LOGS.append(log)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    child = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=flags, start_new_session=os.name != 'nt')
    if WINDOWS_JOB:
        api, handle = WINDOWS_JOB
        if not api.AssignProcessToJobObject(handle, int(child._handle)):
            child.terminate()
            raise RuntimeError('无法管理 Windows 子进程，启动已取消。')
    CHILDREN.append(child)
    return child


def wait_ready(child, url, timeout=300):
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if STOPPING.is_set():
            raise RuntimeError('启动器已关闭')
        if child.poll() is not None:
            raise RuntimeError(f'服务退出（{child.returncode}），请查看 data/logs 日志。')
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f'服务启动超时：{url}；请查看 data/logs 日志。')


def check_ports():
    for port in PORTS:
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError as exc:
                raise RuntimeError(f'端口 {port} 已被占用，请先关闭已有服务。') from exc


def start_services(settings):
    global STATUS, BUSY
    with LOCK:
        try:
            gguf, mmproj = resolve_models(settings)
            check_onnx(settings)
            check_ports()
            env = environment(settings)
            STATUS = '正在加载 CPU GGUF 模型……'
            vlm = spawn('vlm', llama_command(
                '-m', gguf, '--mmproj', mmproj, '--alias', 'MinerU2.5-Pro-2605',
                '--host', '127.0.0.1', '--port', '30000', '--n-gpu-layers', '0',
                '--no-mmproj-offload', '--parallel', '1', '--threads', settings['threads'],
                '--threads-batch', settings['threads'], '--ctx-size', settings['context'],
                '--temp', '0', '--offline', '--special', '--grammar', 'root ::= .*',
                '--no-kv-unified', '--cache-ram', '0'), env)
            wait_ready(vlm, 'http://127.0.0.1:30000/health')
            STATUS = '正在启动 MinerU 文档解析服务……'
            api = spawn('api', command('api-server', '--host', '127.0.0.1', '--port', '8000',
                '--tier', 'standard', '--concurrency', '1', '--upload-dir', ROOT / 'data' / 'uploads',
                '--api-key', LOCAL_API_KEY,
                '--vlm-server-url', 'http://127.0.0.1:30000',
                '--vlm-model', 'MinerU2.5-Pro-2605', '--vlm-max-concurrency', '1',
                '--vlm-http-timeout', '1800'), env)
            wait_ready(api, 'http://127.0.0.1:8000/v1/health')
            STATUS = '正在启动旧浏览器兼容页……'
            compat = spawn('compat', [sys.executable, '-s', str(ROOT / 'compat_server.py'),
                '--host', '127.0.0.1', '--port', str(COMPAT_PORT), '--upstream', 'http://127.0.0.1:8000',
                '--api-key', LOCAL_API_KEY, '--data-dir', str(ROOT / 'data')], env)
            wait_ready(compat, f'http://127.0.0.1:{COMPAT_PORT}/')
            STATUS = '正在启动官方 WebUI……'
            ui = spawn('webui', command('webui', '--server-name', '127.0.0.1',
                '--server-port', '7860', '--api-url', 'http://127.0.0.1:8000'), env)
            wait_ready(ui, 'http://127.0.0.1:7860/')
            STATUS = (f'已就绪。新版浏览器用官方界面 http://127.0.0.1:7860/；'
                      f'UOS 等旧浏览器用兼容页 http://127.0.0.1:{COMPAT_PORT}/。')
            print(STATUS, flush=True)
            if settings.get('open_browser', True):
                webbrowser.open('http://127.0.0.1:7860/')
        except Exception as exc:
            STATUS = str(exc)
            print(STATUS, flush=True)
            stop_children()


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>MinerU 离线启动设置</title><style>body{font:16px system-ui;max-width:840px;margin:40px auto;padding:0 24px;background:#f7f7f7;color:#222}input{box-sizing:border-box;width:100%;padding:10px;margin:8px 0 22px;border:1px solid #bbb;border-radius:6px}input[type=checkbox]{width:auto;padding:0;margin:0 8px 0 0;vertical-align:middle}button,a.button{display:inline-block;padding:12px 20px;background:#164e63;color:white;border:0;border-radius:6px;text-decoration:none}label{display:block}small{color:#555}.status{background:white;padding:18px;margin:24px 0}a{color:#164e63}</style>
<h1>MinerU 离线 CPU 包</h1><p>填写已下载的 MinerU2.5-Pro-2605 GGUF 和配套 mmproj 路径。路径可以包含中文或空格，文件留在原位置即可。</p>
<div class="status">{status}</div><p><a class="button" href="http://127.0.0.1:7860/" target="_blank">打开 MinerU WebUI</a>　<a class="button" href="http://127.0.0.1:7861/" target="_blank">旧浏览器兼容页</a>　<a href="/">刷新启动状态</a></p>
<form method="post" action="/stop"><button>停止本包服务并退出</button></form><br>
<form method="post" action="/start"><label>GGUF 主模型<input name="gguf" value="{gguf}" required placeholder="E:\\models\\MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf"></label>
<label>配套 mmproj<input name="mmproj" value="{mmproj}" required placeholder="/home/user/models/mmproj-MinerU2.5-Pro-2605-1.2B-f16.gguf"></label>
<label>ONNX 模型目录（留空使用包内模型）<input name="onnx_dir" value="{onnx_dir}" placeholder="含 Layout、OCR、MFR、Table 的模型目录"></label>
<label><input type="checkbox" name="seal_clean" value="1" {seal_clean_checked}> 识别前清除红色印章（推荐；红色标题与红色批注保留）</label>
<label>CPU 线程数（5600X 建议 6；兆芯先用 4）<input type="number" name="threads" min="1" max="64" value="{threads}"></label>
<label>上下文长度（2605 配套模型建议 8192）<input type="number" name="context" min="4096" max="8192" step="1024" value="{context}"></label>
<label>任务超时秒数（长文档建议 86400 即 24 小时）<input type="number" name="poll_timeout" min="3600" max="604800" value="{poll_timeout}"></label>
<button>保存并启动</button></form><p><small>首次启动可能需要约 1 分钟。正在运行时修改设置，请先在启动终端按 Ctrl+C，再重新启动。日志位于 data/logs，关闭启动终端会停止包内服务。</small></p></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        settings = read_settings()
        values = {key: html.escape(str(settings.get(key, 86400 if key == 'poll_timeout' else '')), quote=True)
                  for key in ('gguf', 'mmproj', 'onnx_dir', 'threads', 'context', 'poll_timeout')}
        body = PAGE.replace('{status}', html.escape(STATUS))
        for key, value in values.items():
            body = body.replace('{' + key + '}', value)
        state = read_seal_state()
        checked = settings.get('seal_clean', True) if state is None else state
        body = body.replace('{seal_clean_checked}', 'checked' if checked else '')
        body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global STATUS, BUSY
        # 本地配置修改只接受同源页面，防止第三方页面发起表单请求。
        origin = self.headers.get('Origin', '')
        if origin and origin != 'http://127.0.0.1:7862':
            self.send_error(403)
            return
        if self.path == '/stop':
            STOPPING.set()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write('服务正在停止，可以关闭此页。'.encode('utf-8'))
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if BUSY:
            STATUS = '服务正在运行或启动中。要修改路径，请先 Ctrl+C 关闭启动器，再重启。'
            self.do_GET()
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 32768:
                raise ValueError('无效的表单长度')
            form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8'))
            settings = read_settings()
            for key in ('gguf', 'mmproj'):
                settings[key] = form[key][0].strip().strip('"')
            settings['onnx_dir'] = form.get('onnx_dir', [''])[0].strip().strip('"')
            settings['threads'] = int(form['threads'][0])
            settings['context'] = int(form['context'][0])
            settings['poll_timeout'] = int(form.get('poll_timeout', [settings.get('poll_timeout', 86400)])[0])
            settings['seal_clean'] = form.get('seal_clean', [''])[0] == '1'
            write_seal_state(settings['seal_clean'])
            if not 1 <= settings['threads'] <= 64 or not 4096 <= settings['context'] <= 8192 or settings['poll_timeout'] < 3600:
                raise ValueError('线程数、上下文长度或超时设置超出范围')
            resolve_models(settings)
            check_onnx(settings)
            SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
            BUSY = True
            threading.Thread(target=start_services, args=(settings,), daemon=True).start()
        except (ValueError, KeyError, OSError) as exc:
            STATUS = str(exc)
        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()


def self_test():
    for name in ('mineru', 'mineru-llama-cpp', 'onnxruntime', 'gradio'):
        print(name, importlib.metadata.version(name))
    import onnxruntime
    import mineru_llama_cpp
    check_onnx(read_settings())
    result = subprocess.run(llama_command('--version'),
                            env=environment(read_settings()), timeout=60)
    if result.returncode:
        raise RuntimeError('llama-server 二进制自检失败')
    print('运行时与 ONNX 文件自检通过；GGUF 路径需在设置页配置。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if args.check:
        self_test()
        return
    global WINDOWS_JOB
    WINDOWS_JOB = create_windows_job()
    if os.name != 'nt':
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
    settings = read_settings()
    if args.no_browser:
        settings['open_browser'] = False
    environment(settings)
    server = ThreadingHTTPServer(('127.0.0.1', 7862), Handler)
    print(f'设置页：http://127.0.0.1:7862/；WebUI：http://127.0.0.1:7860/；'
          f'旧浏览器兼容页：http://127.0.0.1:{COMPAT_PORT}/', flush=True)
    global BUSY
    if settings.get('gguf') and settings.get('mmproj'):
        BUSY = True
        threading.Thread(target=start_services, args=(settings,), daemon=True).start()
    if settings.get('open_browser', True):
        webbrowser.open('http://127.0.0.1:7862/')
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOPPING.set()
        server.server_close()
        # 等待启动线程退出，避免关闭窗口后仍生成服务。
        with LOCK:
            stop_children()
        if WINDOWS_JOB:
            WINDOWS_JOB[0].CloseHandle(WINDOWS_JOB[1])


if __name__ == '__main__':
    main()
