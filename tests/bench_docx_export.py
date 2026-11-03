"""DOCX 导出性能基准：同一份合成大文档对比补丁前后的渲染耗时。

只在 CI 的独立任务里跑（``.github/workflows/build-test.yml`` 的 ``export-bench``），
只安装 docvortex 与 python-docx，不下载模型、不解析 PDF：

1. 造一份 580 页量级的合成 Middle JSON（正文、标题、列表、跨页续文、表格、公式、
   图片混合，含大量重复公式与复用图片，贴近扫描版书籍的导出负载）；
2. 先按官方路径渲染一次作为基线（不打补丁）；
3. 再装上 ``app/packages/mineru_docx_fast.py`` 的补丁渲染一次；
4. 两次产物都要求能打开、段落数与正文一致、图片字节仍是原图，然后比较耗时。

MinerU 的 ``mineru.render.docx.render_docx`` 只是把调用转给
``docvortex.render.docx.render_docx``，所以这里直接调后者即可复现真实路径。
"""
import argparse
import io
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app' / 'packages'))

PAGE_TEXTS = 22
IMAGE_COUNT = 20
MARKER = 'CPU OCR 1234.56'
# 一半左右是重复公式（变量、符号、常见结论），贴近正文里反复出现的行内公式
FORMULA_POOL = (
    r'E = mc^{2}',
    r'\alpha_{i}',
    r'x^{2} + y^{2} = r^{2}',
    r'\frac{a}{b}',
    r'\sqrt{2}',
    r'\sum_{i=1}^{n} i',
    r'\theta \in \Theta',
    r'O(n \log n)',
    r'\Delta t',
    r'\mu \pm \sigma',
)
DISPLAY_FORMULA = r'\int_{0}^{\infty} e^{-x^{2}} \, dx = \frac{\sqrt{\pi}}{2}'


def build_images(root):
    """生成一批可复用的 JPEG，模拟书籍里的插图与扫描页。"""
    from PIL import Image

    images = root / 'images'
    images.mkdir(parents=True, exist_ok=True)
    for index in range(IMAGE_COUNT):
        image = Image.new('RGB', (1200, 1600), (index * 11 % 255, 200, 60))
        image.save(images / f'figure-{index + 1}.jpg', 'JPEG', quality=90)
    return [f'images/figure-{index + 1}.jpg' for index in range(IMAGE_COUNT)]


def page_bbox(top, height=0.028):
    """页面坐标给成归一化 bbox，固定版式文档的顶层块必须有 bbox。"""
    return [0.1, round(top, 4), 0.9, round(min(0.99, top + height), 4)]


def page_text_spans(page, index):
    """一段正文的若干 Span：普通文字、加粗、链接与行内公式混合。"""
    spans = [{'type': 'text',
              'content': f'第 {page + 1} 页第 {index + 1} 段正文，模拟扫描版书籍的段落，{MARKER}。'}]
    if index % 5 == 1:
        spans.append({'type': 'text', 'content': '加粗片段', 'styles': ['bold']})
    if index % 7 == 2:
        spans.append({'type': 'hyperlink', 'url': f'https://example.com/{page}/{index}',
                      'content': [{'type': 'text', 'content': '参见链接'}]})
    if index % 3 == 0:
        if (page + index) % 3:
            latex = FORMULA_POOL[(page * 7 + index) % len(FORMULA_POOL)]
        else:
            latex = rf'n_{{{page * PAGE_TEXTS + index}}}'
        spans.append({'type': 'equation_inline', 'content': latex})
    return spans


def build_page(page, image_paths):
    """造一页：标题 + 正文 + 列表/表格/公式/图片，尽量覆盖真实排版。"""
    blocks = []
    index = 0
    if page == 0:
        blocks.append({'type': 'doc_title', 'index': index, 'bbox': page_bbox(0.03), 'level': 1,
                       'content': [{'type': 'text', 'content': f'基准文档 {MARKER}'}]})
        index += 1
    blocks.append({'type': 'paragraph_title', 'index': index, 'bbox': page_bbox(0.06), 'level': 2,
                   'anchor': f'page-{page}-title',
                   'content': [{'type': 'text', 'content': f'第 {page + 1} 节 标题'}]})
    index += 1
    for position in range(PAGE_TEXTS):
        block = {'type': 'text', 'index': index, 'bbox': page_bbox(0.09 + position * 0.031),
                 'content': page_text_spans(page, position)}
        if position == PAGE_TEXTS - 1 and page % 4 == 3:
            # 跨页续文：必须被合并进前一块，用来覆盖规划器的合并分支
            block['continues_prev'] = True
        blocks.append(block)
        index += 1
    if page % 7 == 4:
        items = [{'type': 'text', 'index': index, 'bbox': page_bbox(0.79),
                  'content': [{'type': 'text', 'content': f'列表项 {item + 1}'}]} for item in range(5)]
        blocks.append({'type': 'list', 'index': index, 'bbox': page_bbox(0.79, 0.06),
                       'sub_type': 'text', 'content': items})
        index += 1
    if page % 5 == 2:
        rows = ''.join('<tr>' + ''.join(f'<td>单元格 {row}-{column}</td>' for column in range(4)) + '</tr>'
                       for row in range(3))
        table = ('<table><tr><th>列 1</th><th>列 2</th><th>列 3</th><th>列 4</th></tr>' + rows + '</table>')
        blocks.append({'type': 'table', 'index': index, 'bbox': page_bbox(0.86, 0.1), 'content': [
            {'type': 'table_body', 'index': index, 'bbox': page_bbox(0.86, 0.1), 'content': table}]})
        index += 1
    if page % 4 == 1 and page > 1:
        blocks.append({'type': 'equation', 'index': index, 'bbox': page_bbox(0.83),
                       'content': DISPLAY_FORMULA})
        index += 1
    if page % 6 == 3:
        path = image_paths[page % len(image_paths)]
        blocks.append({'type': 'image', 'index': index, 'bbox': page_bbox(0.7, 0.12), 'sub_type': 'figure',
                       'content': [{'type': 'image_body', 'index': index, 'bbox': page_bbox(0.7, 0.12),
                                    'content': f'图 {page + 1} 说明文字', 'image_path': path}]})
        index += 1
    return {'page_idx': page, 'blocks': blocks}


def build_payload(pages, image_paths):
    """组装完整 Middle JSON；schema 与版本必须显式携带。"""
    return {
        'schema': 'docvortex.middle',
        'schema_version': '2.0',
        'metadata': {'file_suffix': 'pdf',
                     'producer': {'name': 'docx-export-bench', 'version': '1.0'}},
        'is_full_document': True,
        'pages': [build_page(page, image_paths) for page in range(pages)],
    }


def make_resolver(assets_root):
    """与兼容页/官方 WebUI 一致：只读任务目录内的安全相对路径。"""
    resolved_root = assets_root.resolve()

    def resolve(relative_path):
        candidate = (resolved_root / relative_path).resolve()
        if not str(candidate).startswith(str(resolved_root)) or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate.read_bytes()

    return resolve


def inspect_docx(data):
    """校验产物可打开，并统计段落、正文与图片成员的压缩方式。"""
    from docx import Document

    document = Document(io.BytesIO(data))
    text = '\n'.join(paragraph.text for paragraph in document.paragraphs)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        media = [info for info in infos if info.filename.startswith('word/media/')]
        stored = sum(1 for info in media if info.compress_type == zipfile.ZIP_STORED)
    return {
        'paragraphs': len(document.paragraphs),
        'tables': len(document.tables),
        'media': len(media),
        'media_stored': stored,
        'images_inline': len(document.inline_shapes),
        'has_marker': MARKER in text,
        'text_length': len(text),
    }


def measure(payload, assets_root, label):
    """把一份文档重新解析并渲染一次，返回耗时与产物自检结果。"""
    from docvortex.render.docx import render_docx
    from docvortex.schema import MiddleJson

    start = time.perf_counter()
    middle_json = MiddleJson.from_dict(payload)
    validate_seconds = time.perf_counter() - start

    start = time.perf_counter()
    data = render_docx(middle_json, asset_resolver=make_resolver(assets_root))
    render_seconds = time.perf_counter() - start
    report = {
        'label': label,
        'validate_seconds': round(validate_seconds, 2),
        'render_seconds': round(render_seconds, 2),
        'output_bytes': len(data),
    }
    report.update(inspect_docx(data))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description='DOCX 导出性能基准')
    parser.add_argument('--pages', type=int, default=580)
    parser.add_argument('--json', dest='output', required=True)
    parser.add_argument('--max-seconds', type=float, default=300.0,
                        help='打补丁后的渲染耗时上限（秒）')
    parser.add_argument('--min-speedup', type=float, default=1.05,
                        help='打补丁后至少要快这么多倍')
    args = parser.parse_args()

    import mineru_docx_fast as docx_fast

    with tempfile.TemporaryDirectory(prefix='docx 基准 ') as directory:
        assets_root = Path(directory)
        image_paths = build_images(assets_root)
        payload = build_payload(args.pages, image_paths)
        blocks = sum(len(page['blocks']) for page in payload['pages'])

        baseline = measure(payload, assets_root, 'baseline')
        # 基线跑完后 docvortex 的 DOCX 门面已在 sys.modules 里，install 会立即挂上包装
        installed = docx_fast.install()
        applied = docx_fast.prepare()
        patched = measure(payload, assets_root, 'patched')
        warm = measure(payload, assets_root, 'patched-warm')

    speedup = baseline['render_seconds'] / max(patched['render_seconds'], 1e-6)
    failures = []
    if not patched['has_marker'] or not baseline['has_marker']:
        failures.append('产物缺少正文标记，渲染结果不完整')
    for key in ('paragraphs', 'images_inline', 'text_length'):
        if baseline[key] != patched[key]:
            failures.append(f'{key} 与基线不一致：{baseline[key]} != {patched[key]}')
    if patched['media'] != baseline['media'] or patched['media'] != patched['media_stored']:
        failures.append(f"图片成员未被原样存放：{patched['media_stored']}/{patched['media']}")
    if speedup < args.min_speedup:
        failures.append(f'加速不足：{speedup:.2f} 倍 < {args.min_speedup} 倍')
    if patched['render_seconds'] > args.max_seconds:
        failures.append(f"打补丁后仍用时 {patched['render_seconds']} 秒 > {args.max_seconds} 秒")

    report = {
        'pages': args.pages,
        'top_level_blocks': blocks,
        'installed': installed,
        'applied_patches': list(applied),
        'speedup': round(speedup, 2),
        'warm_speedup': round(baseline['render_seconds'] / max(warm['render_seconds'], 1e-6), 2),
        'baseline': baseline,
        'patched': patched,
        'patched_warm': warm,
        'failures': failures,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'speedup': report['speedup'], 'warm_speedup': report['warm_speedup'],
                      'applied_patches': report['applied_patches'], 'failures': failures},
                     ensure_ascii=False), flush=True)
    if failures:
        raise SystemExit('DOCX 导出基准未通过：' + '；'.join(failures))


if __name__ == '__main__':
    main()
