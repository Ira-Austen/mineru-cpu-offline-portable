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


def add_red_seal(image):
    """在文字行上叠加半透明红章，用于验证便携包的去章补丁。"""
    import math

    import numpy as np
    from PIL import Image, ImageDraw

    shape = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(shape)
    draw.ellipse([235, 45, 365, 175], outline=255, width=8)
    for angle in range(0, 360, 45):
        radians = math.radians(angle)
        draw.line([300 + 22 * math.cos(radians), 110 + 22 * math.sin(radians),
                   300 + 45 * math.cos(radians), 110 + 45 * math.sin(radians)], fill=255, width=6)
    ratio = (np.asarray(shape).astype('float32') / 255.0) * (170 / 255.0)
    blended = (np.asarray(image).astype('float32') * (1 - ratio[..., None])
               + np.array([228, 26, 34], 'float32') * ratio[..., None])
    return Image.fromarray(np.clip(blended, 0, 255).astype('uint8'))


def check_seal_switch(root_url):
    """确认 WebUI 里出现"识别前清除红色印章"开关，避免界面补丁静默失效。"""
    for path in ('/', '/gradio_api/config'):
        try:
            with OPENER.open(root_url + path, timeout=10) as response:
                body = response.read().decode('utf-8', 'ignore')
        except OSError:
            continue
        if '识别前清除红色印章' in body:
            return path
    raise AssertionError('WebUI 未出现"识别前清除红色印章"开关，界面补丁可能未生效')


def check_compat_parse(pdf_path):
    """通过旧浏览器兼容页真实解析一次，验证这条入口端到端可用。"""
    base = 'http://127.0.0.1:7861'
    with OPENER.open(base + '/', timeout=10) as response:
        page = response.read().decode('utf-8', 'ignore')
    if '兼容旧浏览器' not in page:
        raise AssertionError('兼容页未就绪或内容异常')
    with OPENER.open(base + '/api/capabilities', timeout=15) as response:
        json.load(response)

    upload = urllib.request.Request(
        base + '/api/upload', data=pdf_path.read_bytes(), method='POST',
        headers={'X-Filename': urllib.parse.quote(pdf_path.name)})
    with OPENER.open(upload, timeout=120) as response:
        upload_id = json.load(response)['upload_id']
    start = time.monotonic()
    submit = urllib.request.Request(
        base + '/api/parse', method='POST', headers={'Content-Type': 'application/json'},
        data=json.dumps({'upload_id': upload_id, 'tier': 'standard', 'output_formats': ['markdown']}).encode('utf-8'))
    with OPENER.open(submit, timeout=120) as response:
        task_id = json.load(response)['task_id']

    deadline = time.monotonic() + 900
    markdown = ''
    while time.monotonic() < deadline:
        with OPENER.open(f'{base}/api/task/{task_id}', timeout=30) as response:
            task = json.load(response)
        if task['status'] in ('completed', 'partial'):
            with OPENER.open(f'{base}/api/task/{task_id}/result', timeout=120) as response:
                result = json.load(response)
            markdown = result['files'][0]['content'].get('markdown', '')
            break
        if task['status'] in ('failed', 'canceled'):
            raise AssertionError(f'兼容页解析失败：{task}')
        time.sleep(2)
    else:
        raise TimeoutError('兼容页解析未在 900 秒内完成')
    if '1234.56' not in markdown or 'CPU' not in markdown:
        raise AssertionError(f'兼容页解析结果异常：{markdown}')
    if '<img' in markdown:
        raise AssertionError(f'兼容页结果中出现印章图片块：{markdown}')
    return {'page': True, 'seconds': round(time.monotonic() - start, 2), 'markdown': markdown}


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
            seal_switch_page = check_seal_switch('http://127.0.0.1:7860')
            image = Image.new('RGB', (800, 220), 'white')
            ImageDraw.Draw(image).text((40, 60), 'CPU OCR 1234.56',
                fill='black', font=ImageFont.load_default(size=42))
            # 在文字上叠加一个半透明红章：去章补丁必须把红墨抹掉并保住章下文字
            image = add_red_seal(image)
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
                if '<img' in markdown:
                    raise AssertionError(f'印章未被清除，识别结果中出现图片块：{markdown}')
                item = {'tier': tier, 'seconds': round(time.monotonic() - start, 2), 'markdown': markdown}
                results.append(item)
                print(json.dumps(item), flush=True)
            compat_result = check_compat_parse(pdf)
            print(json.dumps(compat_result), flush=True)
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
    Path(args.output).write_text(json.dumps({'webui': 200, 'seal_switch': seal_switch_page, 'compat': compat_result,
                                             'results': results}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
