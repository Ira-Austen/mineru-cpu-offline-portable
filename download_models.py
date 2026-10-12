"""仅在联网构建机下载固定 ONNX 模型；目标机不会执行此脚本。"""
import argparse
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--dest', required=True)
parser.add_argument('--source', default='modelscope', choices=['modelscope', 'huggingface'])
args = parser.parse_args()
dest = Path(args.dest).resolve()
os.environ['MINERU_MODEL_BASE_DIR'] = str(dest)
os.environ['MINERU_MODEL_SMALL_BACKEND'] = 'onnx'
os.environ['MINERU_MODEL_SOURCE'] = args.source
os.environ['MODELSCOPE_CACHE'] = str(dest.parent / 'modelscope-cache')
os.environ['HF_HOME'] = str(dest.parent / 'hf-cache')
from mineru.model.registry import MINERU_4_MODELS_ONNX

for attempt in range(1, 4):
    try:
        print(f'正在下载/校验 ONNX 模型 (来源: {args.source}, 第 {attempt}/3 次尝试)...', flush=True)
        result = MINERU_4_MODELS_ONNX.ensure(source=args.source)
        print(result, flush=True)
        break
    except Exception as exc:
        if attempt == 3:
            raise
        print(f'ONNX 模型下载遇到异常 ({exc})，5 秒后重试...', flush=True)
        time.sleep(5)
