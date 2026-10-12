"""在解压后便携包上测试官方 WebUI 和扫描 PDF OCR，仅访问回环地址。"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle')
    parser.add_argument('--gguf-dir')
    parser.add_argument('--onnx-dir')
    parser.add_argument('--advanced', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.bundle).resolve()
    sys.path.insert(0, str(root / 'packages'))
    from PIL import Image, ImageDraw, ImageFont

    settings_path = root / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    if args.gguf_dir:
        models = Path(args.gguf_dir).resolve()
        settings['gguf'] = str(models / 'MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf')
        settings['mmproj'] = str(models / 'mmproj-MinerU2.5-Pro-2605-1.2B-f16.gguf')
    if args.onnx_dir:
        settings['onnx_dir'] = str(Path(args.onnx_dir).resolve())
    settings['open_browser'] = False
    settings['threads'] = min(4, os.cpu_count() or 2)
    settings_path.write_text(json.dumps(settings), encoding='utf-8')
    python = root / 'runtime' / 'python' / ('python.exe' if os.name == 'nt' else 'bin/python3.12')
    env = os.environ.copy()
    env.pop('PYTHONHOME', None)
    env['PYTHONPATH'] = str(root / 'packages')
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    if os.name != 'nt':
        env['LD_LIBRARY_PATH'] = f'{root}/runtime/python/lib:{root}/lib'
    subprocess.run([str(python), '-s', str(root / 'launcher.py'), '--check'], env=env, check=True)
    log_path = root / 'data' / 'smoke-launcher.log'
    results = []
    with log_path.open('w', encoding='utf-8') as log:
        child = subprocess.Popen([str(python), '-s', str(root / 'launcher.py'), '--no-browser'],
                                 env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding='utf-8'))
                try:
                    with OPENER.open('http://127.0.0.1:7860/', timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise TimeoutError('官方 WebUI 未在 300 秒内就绪')
            image = Image.new('RGB', (800, 220), 'white')
            ImageDraw.Draw(image).text((40, 60), 'CPU OCR 1234.56',
                fill='black', font=ImageFont.load_default(size=42))
            pdf = root / 'data' / 'smoke-scan.pdf'
            image.save(pdf, 'PDF', resolution=150)
            for tier in (['standard', 'advanced'] if args.advanced else ['standard']):
                start = time.monotonic()
                boundary = 'MinerUPortableSmokeBoundary'
                body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="smoke.pdf"\r\n'
                        'Content-Type: application/pdf\r\n\r\n').encode() + pdf.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
                query = urllib.parse.urlencode({'tier': tier, 'ocr_mode': 'ocr', 'wait_timeout': 900})
                req = urllib.request.Request('http://127.0.0.1:8000/v1/file_parse?' + query, data=body,
                    headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
                with OPENER.open(req, timeout=930) as response:
                    result = json.load(response)
                if result['status'] != 'completed':
                    raise AssertionError(result)
                markdown = result['files'][0]['content']['markdown']
                if '1234.56' not in markdown or 'CPU' not in markdown:
                    raise AssertionError(markdown)
                item = {'tier': tier, 'seconds': round(time.monotonic() - start, 2), 'markdown': markdown}
                results.append(item)
                print(json.dumps(item), flush=True)
        finally:
            try:
                OPENER.open(urllib.request.Request('http://127.0.0.1:7862/stop', data=b''), timeout=5).close()
            except OSError:
                child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    Path(args.output).write_text(json.dumps({'webui': 200, 'results': results}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
