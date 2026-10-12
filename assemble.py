"""从便携运行时、已安装依赖和 ONNX 模型组装两个平台的目录。"""
import argparse
import importlib.metadata
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--work', required=True)
parser.add_argument('--gguf-dir', required=True)
parser.add_argument('--onnx-dir')
args = parser.parse_args()
work = Path(args.work).resolve()
source = Path(__file__).resolve().parent
for platform, runtime_name in [('windows', 'cpython-3.12.13-windows-x86_64-none'),
                               ('linux', 'cpython-3.12.13-linux-x86_64-gnu')]:
    target = work / 'stage' / platform
    for child in (source / 'app').iterdir():
        if child.name == 'packages':
            shutil.copytree(child, target / 'packages', dirs_exist_ok=True)
        elif child.is_file():
            shutil.copy2(child, target / child.name)
    runtime_root = work / ('managed-python' if platform == 'windows' else 'managed-python-linux')
    shutil.copytree(runtime_root / runtime_name, target / 'runtime' / 'python', dirs_exist_ok=True)
    onnx = Path(args.onnx_dir).resolve() if args.onnx_dir else work / 'shared-models' / 'MinerU-4_models_onnx'
    shutil.copytree(onnx,
                    target / 'models' / 'MinerU-4_models_onnx', dirs_exist_ok=True)
    settings = {'gguf': '', 'mmproj': '', 'onnx_dir': '', 'threads': 6 if platform == 'windows' else 4,
                'context': 8192, 'open_browser': True}
    (target / 'settings.json').write_text(json.dumps(settings, indent=2), encoding='utf-8')
    packages = sorted(f'{d.metadata["Name"]}=={d.version}'
                      for d in importlib.metadata.distributions(path=[str(target / 'packages')]))
    (source / f'requirements-{platform}.lock').write_text('\n'.join(packages) + '\n', encoding='utf-8')
    shutil.copy2(source / f'requirements-{platform}.lock', target / 'requirements.lock')
    for name in ('README.md', 'SOURCES.md'):
        if (source / name).exists():
            shutil.copy2(source / name, target / name)
    print(f'组装完成：{target}', flush=True)
print('模型来源：', args.gguf_dir)
