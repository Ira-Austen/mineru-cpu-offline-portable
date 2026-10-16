"""旧浏览器兼容页：为 Chromium 80/90 一类旧内核提供可用的解析入口。

官方 WebUI 由 Gradio 6 构建，前端产物使用了旧内核解析不了或不支持的语法与
API（``??=``、``structuredClone``、顶层 ``await import`` 等），在 UOS20 自带
浏览器上会出现空白与显示不全。本服务只用标准库，提供同源页面：

* 上传文件 → 提交本地 V1 解析任务（任务式，能等待长文档）→ 轮询状态 →
  展示 Markdown/JSON → 按需下载各格式产物；
* 结果里的图片从 zip 产物解压后经本服务提供，Markdown 预览可直接显示；
* 原文件预览（图片直出、PDF 交给浏览器内置查看器）；
* 识别前清除红色印章的开关与启动设置页共用状态文件。

页面脚本只用 ES5/ES2017 语法，避免旧内核报错；服务只监听回环地址。
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'packages'))

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# 本地解析服务只接受这四种产物格式；html/latex/docx 属于云端能力，epub/pdf 由官方页本地渲染。
DEFAULT_OUTPUT_FORMATS = ('markdown', 'middle_json', 'structured_content', 'zip')
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff', '.jp2')

STATE = {
    'upstream': 'http://127.0.0.1:8000',
    'api_key': '',
    'data_dir': ROOT / 'data',
    'uploads_dir': ROOT / 'data' / 'compat-uploads',
    'assets_dir': ROOT / 'data' / 'compat-assets',
}
_ASSET_LOCKS: dict[str, threading.Lock] = {}
_ASSET_LOCK = threading.Lock()
# 与官方界面一致的本地导出格式：按需渲染，不经过解析服务的产物通道。
EXPORT_FORMATS = ('markdown', 'html', 'latex', 'docx', 'epub', 'pdf')
_EXPORT_CACHE: dict[tuple[str, str], tuple[bytes, str]] = {}


def _content_disposition(name):
    """HTTP 头只能编码 latin-1：中文名走 RFC 5987 的 filename*，另给 ASCII 回退名。"""
    suffix = Path(name).suffix
    stem = Path(name).stem.encode('ascii', 'ignore').decode('ascii').strip()
    fallback = f'{stem or "download"}{suffix}'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"
_EXPORT_RENDERER = None


def _renderer():
    """惰性加载 MinerU 本地渲染器，只有真正导出时才付出导入成本。"""
    global _EXPORT_RENDERER
    if _EXPORT_RENDERER is None:
        from mineru.parser.base import ParseResult
        from mineru.render import RenderFormat, render
        from mineru.render.contracts import (
            DocxRenderOptions,
            EpubRenderOptions,
            HtmlRenderOptions,
            LatexRenderOptions,
            MarkdownRenderOptions,
            PdfRenderOptions,
        )

        _EXPORT_RENDERER = {
            'ParseResult': ParseResult,
            'render': render,
            'markdown': (RenderFormat.MARKDOWN, MarkdownRenderOptions),
            'html': (RenderFormat.HTML, HtmlRenderOptions),
            'latex': (RenderFormat.LATEX, LatexRenderOptions),
            'docx': (RenderFormat.DOCX, DocxRenderOptions),
            'epub': (RenderFormat.EPUB, EpubRenderOptions),
            'pdf': (RenderFormat.PDF, PdfRenderOptions),
        }
    return _EXPORT_RENDERER


def _asset_resolver(root):
    """导出时从任务目录读取图片素材，拒绝越界路径。"""
    resolved_root = Path(root).resolve()

    def resolve(relative_path):
        candidate = (resolved_root / str(relative_path)).resolve()
        if not str(candidate).startswith(str(resolved_root)) or not candidate.is_file():
            raise FileNotFoundError(str(relative_path))
        return candidate.read_bytes()

    return resolve


def export_document(format_name, middle_json, assets_root, base_url):
    """用官方同款渲染器生成指定格式，返回 (字节, Content-Type)。"""
    engine = _renderer()
    if format_name not in EXPORT_FORMATS:
        raise ValueError(f'不支持的导出格式：{format_name}')
    result = engine['ParseResult'].from_dict(middle_json)
    render_format, options_class = engine[format_name]
    render = engine['render']
    if format_name in ('docx', 'epub', 'pdf'):
        payload = render(result.middle_json, render_format,
                         options=options_class(asset_resolver=_asset_resolver(assets_root)))
    elif format_name == 'latex':
        payload = render(result.middle_json, render_format,
                         options=options_class(asset_base_path=str(assets_root)))
    else:
        payload = render(result.middle_json, render_format, options=options_class(asset_base_url=base_url))
    content_types = {
        'markdown': 'text/markdown; charset=utf-8',
        'html': 'text/html; charset=utf-8',
        'latex': 'application/x-tex; charset=utf-8',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'epub': 'application/epub+zip',
        'pdf': 'application/pdf',
    }
    data = payload.encode('utf-8') if isinstance(payload, str) else payload
    return data, content_types.get(format_name, 'application/octet-stream')


def build_page() -> str:
    """兼容页 HTML；脚本为 ES5 语法，不使用旧内核不支持的特性。"""
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MinerU 离线解析（兼容旧浏览器）</title>
<style>
body{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f4f5f7;color:#1f2933}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:20px;margin:0 0 8px}
h2{font-size:16px;margin:24px 0 8px}
.hint{color:#52606d;font-size:13px;margin:0 0 20px}
.card{background:#fff;border:1px solid #d9dee5;border-radius:8px;padding:16px;margin-bottom:16px}
label{display:block;margin:10px 0 2px;font-weight:600;font-size:13px}
label.inline{display:inline-block;font-weight:400;margin-right:16px}
input[type=text],select{width:100%;box-sizing:border-box;padding:8px;border:1px solid #b7c0cc;border-radius:6px;font-size:14px}
input[type=file]{font-size:14px}
button{font-size:14px;padding:9px 18px;border:0;border-radius:6px;background:#164e63;color:#fff;cursor:pointer}
button[disabled]{background:#9aa5b1;cursor:default}
button.secondary{background:#e4e7eb;color:#1f2933}
.status{margin-top:12px;font-size:14px;color:#323f4b;white-space:pre-wrap}
.error{color:#9b1c1c}
pre{background:#f9fafb;border:1px solid #e4e7eb;border-radius:6px;padding:12px;overflow:auto;max-height:420px;white-space:pre-wrap;word-break:break-word}
.downloads a{display:inline-block;margin:0 8px 8px 0;padding:6px 12px;border:1px solid #b7c0cc;border-radius:6px;text-decoration:none;color:#164e63;font-size:13px}
#previewImg{max-width:100%;border:1px solid #d9dee5;border-radius:6px}
#previewPdf{width:100%;height:640px;border:1px solid #d9dee5;border-radius:6px}
.muted{color:#7b8794;font-size:12px}
</style>
</head>
<body>
<main>
  <h1>MinerU 离线解析（兼容旧浏览器）</h1>
  <p class="hint">本页专门兼容 Chromium 80/90 等旧内核（例如 UOS 自带浏览器）。功能覆盖上传、档位、强制 OCR、页码范围、结果查看与多格式下载、原文件预览和去印章开关；新版浏览器可直接使用官方界面 <span class="muted">http://127.0.0.1:7860/</span>。</p>

  <div class="card">
    <label for="file">选择文档（PDF、图片、Office、EPUB、HTML/OFD、CSV 等）</label>
    <input type="file" id="file">
    <label for="tier">解析档位</label>
    <select id="tier"></select>
    <label>选项</label>
    <span><label class="inline" for="forceOcr"><input type="checkbox" id="forceOcr"> 强制 OCR（忽略文本层）</label>
    <label class="inline" for="sealClean"><input type="checkbox" id="sealClean"> 识别前清除红色印章</label></span>
    <label for="pageRange">页码范围（可选，仅 PDF，例如 1-5,8）</label>
    <input type="text" id="pageRange" placeholder="留空表示全部页面">
    <p><button id="parseBtn">开始解析</button> <button id="cancelBtn" class="secondary" disabled>取消</button></p>
    <div class="status" id="status">等待上传文件。</div>
  </div>

  <div class="card" id="previewCard" style="display:none">
    <h2>原文件预览</h2>
    <div id="preview"></div>
  </div>

  <div class="card" id="resultCard" style="display:none">
    <h2>下载结果</h2>
    <div class="downloads" id="downloads"></div>
    <h2>其他格式（本地渲染，与官方界面同一套渲染器）</h2>
    <div class="downloads" id="exports"></div>
    <p class="muted">HTML / DOCX / LaTeX / EPUB / PDF 按需生成，首次点击可能需要几秒；若某种格式渲染失败，其余格式与上面的下载不受影响。</p>
    <h2>Markdown</h2>
    <pre id="markdown"></pre>
    <h2>JSON</h2>
    <pre id="json"></pre>
  </div>
</main>
<script>
(function () {
  var el = function (id) { return document.getElementById(id); };
  var state = { uploadId: '', taskId: '', resultTaskId: '', fileName: '', timer: null, started: 0, polling: false };
  var FORMAT_LABELS = { markdown: 'Markdown', middle_json: 'JSON', structured_content: '结构化 JSON', zip: 'ZIP（含图片）' };

  function setStatus(text, isError) {
    el('status').className = isError ? 'status error' : 'status';
    el('status').textContent = text;
  }

  function request(method, url, body, contentType) {
    return new Promise(function (resolve, reject) {
      var options = { method: method };
      if (body !== undefined && body !== null) { options.body = body; }
      if (contentType) { options.headers = { 'Content-Type': contentType }; }
      fetch(url, options).then(function (response) {
        return response.text().then(function (text) {
          var payload = null;
          try { payload = text ? JSON.parse(text) : null; } catch (error) { payload = null; }
          if (!response.ok) {
            var message = payload && payload.error && payload.error.message ? payload.error.message : ('HTTP ' + response.status);
            reject(new Error(message));
            return;
          }
          resolve(payload);
        });
      }).catch(reject);
    });
  }

  function loadCapabilities() {
    request('GET', '/api/capabilities').then(function (data) {
      var select = el('tier');
      select.innerHTML = '';
      var tiers = data.tiers && data.tiers.length ? data.tiers : ['standard'];
      for (var i = 0; i < tiers.length; i += 1) {
        var option = document.createElement('option');
        option.value = tiers[i].id;
        option.textContent = tiers[i].label;
        if (tiers[i].id === data.default_tier) { option.selected = true; }
        select.appendChild(option);
      }
      el('sealClean').checked = !!data.seal_clean;
      el('sealClean').disabled = !data.seal_clean_available;
      if (data.notice) { setStatus(data.notice); }
    }).catch(function (error) {
      setStatus('无法连接解析服务：' + error.message, true);
    });
  }

  function showPreview(file) {
    var container = el('preview');
    container.innerHTML = '';
    el('previewCard').style.display = 'block';
    var name = (file.name || '').toLowerCase();
    var url = '/api/preview?id=' + encodeURIComponent(state.uploadId);
    if (/\.(png|jpg|jpeg|webp|gif|bmp|tiff|jp2)$/.test(name)) {
      var image = document.createElement('img');
      image.id = 'previewImg';
      image.src = url;
      container.appendChild(image);
    } else if (/\.pdf$/.test(name)) {
      var embed = document.createElement('embed');
      embed.id = 'previewPdf';
      embed.type = 'application/pdf';
      embed.src = url;
      container.appendChild(embed);
    } else {
      var link = document.createElement('a');
      link.href = url;
      link.textContent = '下载原文件以查看内容（该格式暂不支持页面内预览）';
      container.appendChild(link);
    }
  }

  function renderDownloads(files) {
    var container = el('downloads');
    container.innerHTML = '';
    var shown = false;
    for (var i = 0; i < files.length; i += 1) {
      var file = files[i];
      var outputs = file.output_files || {};
      for (var format in outputs) {
        if (!outputs.hasOwnProperty(format) || !outputs[format]) { continue; }
        var ref = outputs[format];
        var link = document.createElement('a');
        link.href = '/api/file/' + encodeURIComponent(ref.file_id) + '?name=' + encodeURIComponent(file.name + '.' + format);
        link.textContent = FORMAT_LABELS[format] || format;
        container.appendChild(link);
        shown = true;
      }
    }
    if (!shown) { container.textContent = '本次结果没有可下载的产物。'; }
  }

  function renderExports() {
    var container = el('exports');
    container.innerHTML = '';
    var taskId = state.resultTaskId;
    if (!taskId) { return; }
    var stem = (state.fileName || 'result').replace(/\.[^.]+$/, '');
    var labels = [['html', 'HTML'], ['docx', 'DOCX'], ['latex', 'LaTeX'], ['epub', 'EPUB'], ['pdf', 'PDF']];
    for (var i = 0; i < labels.length; i += 1) {
      var link = document.createElement('a');
      link.href = '/api/export/' + encodeURIComponent(taskId) + '?format=' + labels[i][0]
        + '&name=' + encodeURIComponent(stem + '.' + labels[i][0]);
      link.textContent = labels[i][1];
      container.appendChild(link);
    }
  }

  function renderResult(result) {
    var files = result.files || [];
    if (!files.length) { return; }
    var file = files[0];
    if (file.status !== 'completed' && file.status !== 'partial') {
      var detail = file.error && file.error.message ? file.error.message : file.status;
      setStatus('解析失败：' + detail, true);
      return;
    }
    var content = file.content || {};
    el('markdown').textContent = content.markdown || '（本次结果没有 Markdown 内容）';
    el('json').textContent = content.middle_json ? JSON.stringify(content.middle_json, null, 2)
      : (content.structured_content ? JSON.stringify(content.structured_content, null, 2) : '（本次结果没有 JSON 内容）');
    renderDownloads(files);
    renderExports();
    el('resultCard').style.display = 'block';
    var seconds = Math.round((Date.now() - state.started) / 1000);
    setStatus('解析完成，用时 ' + seconds + ' 秒。', false);
  }

  function poll() {
    if (!state.taskId) { return; }
    request('GET', '/api/task/' + encodeURIComponent(state.taskId)).then(function (task) {
      if (!state.taskId) { return; }
      var status = task.status;
      var elapsed = Math.round((Date.now() - state.started) / 1000);
      if (status === 'completed' || status === 'partial') {
        setStatus('解析完成，正在获取结果…（已用 ' + elapsed + ' 秒）');
        var finishedTask = state.taskId;
        request('GET', '/api/task/' + encodeURIComponent(state.taskId) + '/result').then(function (result) {
          state.polling = false;
          state.taskId = '';
          state.resultTaskId = finishedTask;
          el('cancelBtn').disabled = true;
          el('parseBtn').disabled = false;
          renderResult(result);
        }).catch(function (error) {
          state.polling = false;
          el('parseBtn').disabled = false;
          setStatus('获取结果失败：' + error.message, true);
        });
        return;
      }
      if (status === 'failed' || status === 'canceled') {
        state.polling = false;
        state.taskId = '';
        el('cancelBtn').disabled = true;
        el('parseBtn').disabled = false;
        setStatus(status === 'canceled' ? '任务已取消。' : '解析失败，请查看服务日志。', true);
        return;
      }
      setStatus(status === 'running' ? ('解析中…（已用 ' + elapsed + ' 秒）') : ('排队中…（已用 ' + elapsed + ' 秒）'));
      state.timer = window.setTimeout(poll, 2000);
    }).catch(function (error) {
      state.timer = window.setTimeout(poll, 4000);
      setStatus('状态查询失败，正在重试：' + error.message, true);
    });
  }

  function upload(file) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload');
      xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name || 'upload'));
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) { return; }
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); } catch (error) { reject(new Error('上传响应无法解析')); }
        } else {
          reject(new Error(xhr.responseText || ('HTTP ' + xhr.status)));
        }
      };
      xhr.send(file);
    });
  }

  function startParse() {
    var input = el('file');
    if (!input.files || !input.files.length) {
      setStatus('请先选择要解析的文件。', true);
      return;
    }
    var file = input.files[0];
    state.fileName = file.name || 'result';
    state.resultTaskId = '';
    el('resultCard').style.display = 'none';
    el('downloads').innerHTML = '';
    el('markdown').textContent = '';
    el('json').textContent = '';
    el('parseBtn').disabled = true;
    setStatus('正在上传文件…（' + Math.round(file.size / 1024) + ' KB）');
    state.started = Date.now();
    state.polling = true;
    upload(file).then(function (uploaded) {
      state.uploadId = uploaded.upload_id;
      showPreview(file);
      setStatus('已上传，正在提交解析任务…');
      var formats = ['markdown', 'middle_json'];
      var body = JSON.stringify({
        upload_id: uploaded.upload_id,
        tier: el('tier').value,
        force_ocr: el('forceOcr').checked,
        page_range: el('pageRange').value,
        output_formats: formats
      });
      return request('POST', '/api/parse', body, 'application/json');
    }).then(function (task) {
      state.taskId = task.task_id;
      el('cancelBtn').disabled = false;
      setStatus('任务已提交，正在解析…');
      state.timer = window.setTimeout(poll, 1500);
    }).catch(function (error) {
      state.polling = false;
      state.taskId = '';
      el('parseBtn').disabled = false;
      el('cancelBtn').disabled = true;
      setStatus('提交失败：' + error.message, true);
    });
  }

  function cancelParse() {
    if (!state.taskId) { return; }
    var taskId = state.taskId;
    setStatus('正在取消任务…');
    request('POST', '/api/task/' + encodeURIComponent(taskId) + '/cancel').catch(function () {}).then(function () {
      state.taskId = '';
      state.polling = false;
      el('cancelBtn').disabled = true;
      el('parseBtn').disabled = false;
      setStatus('任务已取消。');
    });
  }

  el('parseBtn').onclick = startParse;
  el('cancelBtn').onclick = cancelParse;
  el('sealClean').onchange = function () {
    var body = JSON.stringify({ enabled: el('sealClean').checked });
    request('POST', '/api/seal-clean', body, 'application/json').then(function () {
      setStatus('去印章设置已更新：' + (el('sealClean').checked ? '开启' : '关闭'));
    }).catch(function (error) {
      setStatus('去印章设置更新失败：' + error.message, true);
    });
  };
  loadCapabilities();
})();
</script>
</body>
</html>
"""


class CompatHandler(BaseHTTPRequestHandler):
    """把兼容页请求转发到本地 V1 API，并管理上传、产物与去章开关。"""

    server_version = 'MinerU-Compat/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    # ── 基础工具 ────────────────────────────────────────────────────
    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status, text):
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._json(status, {'error': {'message': message}})

    def _upstream(self, method, path, body=None, content_type=None, timeout=600):
        request = urllib.request.Request(STATE['upstream'] + path, data=body, method=method)
        if content_type:
            request.add_header('Content-Type', content_type)
        if STATE['api_key']:
            request.add_header('Authorization', 'Bearer ' + STATE['api_key'])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except urllib.error.URLError as exc:
            return 502, {}, json.dumps({'error': {'message': f'无法连接解析服务：{exc.reason}'}}).encode('utf-8')

    def _read_body(self, limit=MAX_UPLOAD_BYTES):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return b''
        if length > limit:
            raise ValueError('请求体过大')
        return self.rfile.read(length)

    # ── GET ─────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if route in ('/', '/index.html'):
            body = build_page().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if route == '/api/capabilities':
            self._capabilities()
            return
        if route == '/api/seal-clean':
            from mineru_seal_clean import read_state

            state = read_state()
            self._json(200, {'enabled': True if state is None else state})
            return
        if route.startswith('/api/task/'):
            self._task_get(route)
            return
        if route.startswith('/api/file/'):
            self._file_download(route, query)
            return
        if route == '/api/preview':
            self._preview(query)
            return
        if route.startswith('/api/asset/'):
            self._asset(route)
            return
        if route.startswith('/api/export/'):
            self._export(route, query)
            return
        self._error(404, '未知路径')

    def _capabilities(self):
        status, _, body = self._upstream('GET', '/v1/tiers', timeout=30)
        tiers = []
        default_tier = None
        if status == 200:
            try:
                for item in json.loads(body.decode('utf-8')).get('data', []):
                    tier_id = str(item.get('id', ''))
                    if not tier_id:
                        continue
                    tiers.append({'id': tier_id, 'label': self._tier_label(tier_id, item.get('description'))})
            except (ValueError, AttributeError):
                tiers = []
        if not tiers:
            tiers = [{'id': tier_id, 'label': self._tier_label(tier_id, None)} for tier_id in ('standard', 'advanced')]
        for candidate in ('standard', 'basic', 'advanced', 'flash'):
            if any(item['id'] == candidate for item in tiers):
                default_tier = candidate
                break
        from mineru_seal_clean import read_state

        state = read_state()
        self._json(200, {
            'tiers': tiers,
            'default_tier': default_tier or tiers[0]['id'],
            'seal_clean': True if state is None else state,
            'seal_clean_available': True,
            'notice': '',
        })

    @staticmethod
    def _tier_label(tier_id, description):
        labels = {
            'flash': 'flash（极速）',
            'basic': 'basic（中）',
            'standard': 'standard（高）',
            'advanced': 'advanced（极高）',
        }
        return labels.get(tier_id, description or tier_id)

    def _task_get(self, route):
        parts = [part for part in route.split('/') if part]
        # /api/task/<id> 或 /api/task/<id>/result
        if len(parts) < 3:
            self._error(404, '未知任务路径')
            return
        task_id = urllib.parse.quote(parts[2], safe='')
        if len(parts) == 3:
            status, _, body = self._upstream('GET', f'/v1/tasks/{task_id}', timeout=60)
            self._passthrough_json(status, body)
            return
        if parts[3] == 'result':
            status, _, body = self._upstream('GET', f'/v1/tasks/{task_id}/result', timeout=600)
            if status == 200:
                body = self._with_local_assets(task_id, body)
                self._passthrough_json(200, body)
                return
            self._passthrough_json(status, body)
            return
        self._error(404, '未知任务路径')

    def _passthrough_json(self, status, body):
        try:
            payload = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            self._error(502, '解析服务返回了无法识别的内容')
            return
        self._json(status, payload)

    def _file_download(self, route, query):
        parts = [part for part in route.split('/') if part]
        if len(parts) < 3:
            self._error(404, '未知文件路径')
            return
        file_id = urllib.parse.quote(parts[2], safe='')
        status, headers, body = self._upstream('GET', f'/v1/files/{file_id}/content', timeout=600)
        if status != 200:
            self._error(status, '下载失败')
            return
        name = (query.get('name') or ['download'])[0]
        name = os.path.basename(name).replace('"', '')
        content_type = headers.get('Content-Type') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Disposition', _content_disposition(name))
        self.end_headers()
        self.wfile.write(body)

    def _preview(self, query):
        upload_id = (query.get('id') or [''])[0]
        path = self._upload_path(upload_id)
        if path is None:
            self._error(404, '找不到上传的文件')
            return
        self._send_file(path)

    def _asset(self, route):
        parts = [part for part in route.split('/') if part]
        if len(parts) < 4:
            self._error(404, '未知资源路径')
            return
        task_id = parts[2]
        relative = '/'.join(parts[3:])
        root = (STATE['assets_dir'] / task_id).resolve()
        target = (root / relative).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            self._error(404, '找不到资源')
            return
        self._send_file(target)

    def _export(self, route, query):
        """按需把任务结果本地渲染成 HTML/DOCX/LaTeX/EPUB/PDF 等格式。"""
        parts = [part for part in route.split('/') if part]
        if len(parts) < 3:
            self._error(404, '未知导出路径')
            return
        task_id = parts[2]
        format_name = (query.get('format') or [''])[0].lower()
        if format_name not in EXPORT_FORMATS:
            self._error(400, '不支持的导出格式')
            return
        default_name = f'{task_id}.{format_name}'
        name = os.path.basename((query.get('name') or [default_name])[0]) or default_name
        host = self.headers.get('Host') or '127.0.0.1:7861'
        base_url = f'http://{host}/api/asset/{task_id}/'
        cached = _EXPORT_CACHE.get((task_id, format_name))
        if cached is None:
            cached = self._render_export(task_id, format_name, base_url)
            if cached is None:
                return
            _EXPORT_CACHE[(task_id, format_name)] = cached
        data, content_type = cached
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Disposition', _content_disposition(name))
        self.end_headers()
        self.wfile.write(data)

    def _render_export(self, task_id, format_name, base_url):
        """取回任务结果与图片素材并渲染；失败时已回写错误响应并返回 None。"""
        quoted = urllib.parse.quote(task_id, safe='')
        status, _, body = self._upstream('GET', f'/v1/tasks/{quoted}/result', timeout=600)
        if status != 200:
            self._error(status if status >= 400 else 502, '任务结果不可用，无法导出')
            return None
        try:
            payload = json.loads(body.decode('utf-8'))
            files = payload.get('files') or []
            content = (files[0].get('content') or {}) if files else {}
            middle_json = content.get('middle_json')
            outputs = (files[0].get('output_files') or {}) if files else {}
        except (ValueError, AttributeError, IndexError, UnicodeDecodeError):
            self._error(502, '任务结果无法解析')
            return None
        if not isinstance(middle_json, dict):
            self._error(502, '任务结果缺少 Middle JSON，无法本地渲染该格式')
            return None
        assets_root = STATE['assets_dir'] / task_id
        if not assets_root.is_dir():
            zip_ref = outputs.get('zip') or {}
            self._extract_assets(task_id, zip_ref.get('file_id'), assets_root)
        try:
            data, content_type = export_document(format_name, middle_json, assets_root, base_url)
        except Exception as exc:  # 渲染失败不能影响其他格式与既有下载
            self._error(501, f'本地渲染 {format_name} 失败：{type(exc).__name__}: {exc}')
            return None
        return data, content_type

    def _send_file(self, path):
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        self.wfile.write(data)

    # ── POST ────────────────────────────────────────────────────────
    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        try:
            if route == '/api/upload':
                self._upload()
                return
            if route == '/api/parse':
                self._parse()
                return
            if route == '/api/seal-clean':
                self._set_seal_clean()
                return
            if route.startswith('/api/task/') and route.endswith('/cancel'):
                parts = [part for part in route.split('/') if part]
                task_id = urllib.parse.quote(parts[2], safe='')
                status, _, body = self._upstream('DELETE', f'/v1/tasks/{task_id}', timeout=60)
                self._passthrough_json(status if status >= 400 else 200, body)
                return
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except OSError as exc:
            self._error(500, f'本地处理失败：{exc}')
            return
        self._error(404, '未知路径')

    def _upload(self):
        raw_name = urllib.parse.unquote(self.headers.get('X-Filename') or 'upload')
        name = os.path.basename(raw_name) or 'upload'
        suffix = Path(name).suffix.lower()
        data = self._read_body()
        if not data:
            raise ValueError('上传内容为空')
        STATE['uploads_dir'].mkdir(parents=True, exist_ok=True)
        upload_id = uuid.uuid4().hex
        path = STATE['uploads_dir'] / f'{upload_id}{suffix}'
        path.write_bytes(data)
        (STATE['uploads_dir'] / f'{upload_id}.name').write_text(name, encoding='utf-8')
        self._json(200, {'upload_id': upload_id, 'bytes': len(data), 'is_image': suffix in IMAGE_SUFFIXES})

    def _parse(self):
        body = json.loads(self._read_body(limit=1024 * 1024).decode('utf-8'))
        upload_id = str(body.get('upload_id') or '')
        path = self._upload_path(upload_id)
        if path is None:
            raise ValueError('上传已失效，请重新选择文件')
        name = (STATE['uploads_dir'] / f'{upload_id}.name')
        filename = name.read_text(encoding='utf-8') if name.is_file() else path.name
        formats = [fmt for fmt in body.get('output_formats') or DEFAULT_OUTPUT_FORMATS]
        if 'zip' not in formats:
            # zip 提供图片素材：页面预览、资源路由与本地导出都依赖它。
            formats.append('zip')
        params = {
            'filename': filename,
            'ocr_mode': 'ocr' if body.get('force_ocr') else 'auto',
            'output_formats': formats,
        }
        if body.get('tier'):
            params['tier'] = str(body['tier'])
        page_range = str(body.get('page_range') or '').strip()
        if page_range:
            params['page_range'] = page_range
        query = urllib.parse.urlencode(params, doseq=True)
        status, _, response = self._upstream(
            'POST', f'/v1/tasks?{query}', body=path.read_bytes(), content_type='application/octet-stream', timeout=600)
        if status >= 400:
            try:
                detail = json.loads(response.decode('utf-8'))
            except ValueError:
                detail = {'error': {'message': '提交任务失败'}}
            self._json(400, detail)
            return
        try:
            payload = json.loads(response.decode('utf-8'))
        except ValueError:
            self._error(502, '解析服务返回了无法识别的内容')
            return
        self._json(200, {'task_id': payload.get('task_id') or payload.get('job_id')})

    def _set_seal_clean(self):
        body = json.loads(self._read_body(limit=4096).decode('utf-8'))
        from mineru_seal_clean import write_state

        enabled = bool(body.get('enabled'))
        if not write_state(enabled):
            raise OSError('状态文件写入失败')
        self._json(200, {'enabled': enabled})

    # ── 产物本地化 ──────────────────────────────────────────────────
    def _upload_path(self, upload_id):
        if not re.fullmatch(r'[0-9a-f]{32}', upload_id or ''):
            return None
        candidates = list(STATE['uploads_dir'].glob(upload_id + '.*'))
        for candidate in candidates:
            if candidate.suffix != '.name' and candidate.is_file():
                return candidate
        return None

    def _with_local_assets(self, task_id, body):
        """把结果里的 zip 解压到本地，并把 Markdown 的图片路径改写到本地资源路由。"""
        try:
            payload = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return body
        files = payload.get('files') or []
        if not files:
            return body
        for file in files:
            outputs = file.get('output_files') or {}
            zip_ref = outputs.get('zip')
            if not zip_ref:
                continue
            root = STATE['assets_dir'] / task_id
            if not root.is_dir():
                self._extract_assets(task_id, zip_ref.get('file_id'), root)
            content = file.get('content') or {}
            markdown = content.get('markdown')
            if isinstance(markdown, str) and root.is_dir():
                content['markdown'] = self._rewrite_markdown(task_id, markdown)
                file['content'] = content
        return json.dumps(payload, ensure_ascii=False).encode('utf-8')

    def _extract_assets(self, task_id, file_id, root):
        if not file_id:
            return
        with _ASSET_LOCK:
            lock = _ASSET_LOCKS.setdefault(task_id, threading.Lock())
        with lock:
            if root.is_dir():
                return
            status, _, data = self._upstream('GET', f'/v1/files/{urllib.parse.quote(file_id, safe="")}/content', timeout=600)
            if status != 200:
                return
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                return
            target_root = STATE['assets_dir'] / task_id
            target_root.mkdir(parents=True, exist_ok=True)
            for info in archive.infolist():
                name = Path(info.filename)
                if name.is_absolute() or '..' in name.parts or '\\' in info.filename:
                    continue
                destination = (target_root / name).resolve()
                if not str(destination).startswith(str(target_root.resolve())):
                    continue
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))

    def _rewrite_markdown(self, task_id, markdown):
        prefix = f'/api/asset/{task_id}/'
        rewritten = re.sub(r'(!\[[^\]]*\]\()(\./)?images/', lambda match: match.group(1) + prefix + 'images/', markdown)
        rewritten = re.sub(r'(<img[^>]*src=")(\./)?images/', lambda match: match.group(1) + prefix + 'images/', rewritten)
        return rewritten


def create_server(host, port, upstream, api_key, data_dir):
    """按参数装配服务实例，便于测试使用随机端口与临时数据目录。"""
    STATE['upstream'] = upstream.rstrip('/')
    STATE['api_key'] = api_key
    STATE['data_dir'] = Path(data_dir).resolve()
    STATE['uploads_dir'] = STATE['data_dir'] / 'compat-uploads'
    STATE['assets_dir'] = STATE['data_dir'] / 'compat-assets'
    STATE['uploads_dir'].mkdir(parents=True, exist_ok=True)
    STATE['assets_dir'].mkdir(parents=True, exist_ok=True)
    with _ASSET_LOCK:
        _ASSET_LOCKS.clear()
    _EXPORT_CACHE.clear()
    return ThreadingHTTPServer((host, port), CompatHandler)


def serve(host, port, upstream, api_key, data_dir):
    server = create_server(host, port, upstream, api_key, data_dir)
    host, port = server.server_address[:2]
    print(f'兼容页已就绪：http://{host}:{port}/（上游 {STATE["upstream"]}）', flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7861)
    parser.add_argument('--upstream', default=os.environ.get('MINERU_PORTABLE_API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--api-key', default=os.environ.get('MINERU_API_KEY', ''))
    parser.add_argument('--data-dir', default=str(ROOT / 'data'))
    args = parser.parse_args()
    serve(args.host, args.port, args.upstream, args.api_key, args.data_dir)


if __name__ == '__main__':
    main()
