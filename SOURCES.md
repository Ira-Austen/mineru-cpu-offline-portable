# 构建参考与组件来源

- Ira-Austen UOS 离线包 Action： https://github.com/Ira-Austen/umi-ocr-linux-offline/blob/main/.github/workflows/build-offline-linux.yml
- MinerU： https://github.com/opendatalab/MinerU
- 固定 PyPI 版本： https://pypi.org/project/mineru/4.0.12/
- 便携 Python： https://github.com/astral-sh/python-build-standalone
- ONNX 模型： https://modelscope.cn/models/OpenDataLab/MinerU-4_models_onnx
- 对应 Hugging Face： https://huggingface.co/opendatalab/MinerU-4_models_onnx
- 原始 MinerU2.5-Pro 模型： https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B
- GGUF： https://huggingface.co/42ailab/MinerU2.5-Pro-2605-1.2B-GGUF 。已通过远端 LFS SHA256 核对，与用户已有的 Q4_K_M / F16 两文件逐字节一致；固定版本与哈希见 `gguf-models.json`。
- Actions 缓存规则： https://github.com/actions/cache 。模型缓存键独立于程序依赖；GitHub 缓存可能被淘汰。

本实现参考便携 Python、环境隔离和 ELF/glibc 审计思路，没有复用 Umi-OCR 的宿主补丁或插件。
