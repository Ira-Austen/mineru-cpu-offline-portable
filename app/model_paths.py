"""把固定 ONNX 仓库映射到用户目录，下载锁仍写入包内 data。"""
import os
from pathlib import Path


def configure_model_paths():
    directory = os.environ.get('MINERU_PORTABLE_ONNX_DIR')
    if not directory:
        return
    from mineru.model.download import ModelRepo
    original = ModelRepo.local_dir

    def local_dir(repo):
        if repo.name == 'MinerU-4_models_onnx':
            return Path(directory)
        return original(repo)

    ModelRepo.local_dir = local_dir
