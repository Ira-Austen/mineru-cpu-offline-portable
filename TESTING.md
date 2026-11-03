# 构建与测试记录

GitHub Actions 已实际构建六个离线包，并完成 Windows 与 Linux CPU 推理测试。

- 最新成功运行：https://github.com/Ira-Austen/mineru-cpu-offline-portable/actions/runs/41662774726
- 测试源码：`7166d2ca215d0f017d50ea80ab0a15f5385698aa`
- 固定版本：MinerU 4.0.12、mineru-llama-cpp 0.1.3、Python 3.12.13。
- `build`、`linux-check` 与 `export-bench` 三个任务均为 `success`。
- 更早的一次记录：https://github.com/Ira-Austen/mineru-cpu-offline-portable/actions/runs/38288783915（源码 `714c0df96cfd3ba059a808b3438f6309a28ce07f`）。

## 验证范围

构建任务通过 69 个单元测试、六个归档的逐文件 SHA256 校验、Linux ELF 的 glibc 符号审计，以及 Windows 中文空格路径下的官方 WebUI、扫描 PDF OCR 与兼容页 DOCX 导出。完整包测试 Standard、Advanced；Runtime 包配置已有 GGUF、mmproj、ONNX 路径后测试 Standard。

Linux 测试在 Debian 10（glibc 2.28）容器内执行，使用 UID 1000、`--network none`，完整包模型目录为只读。Full 与 Runtime 的自检、官方 WebUI、实际 OCR 均通过；Runtime 使用完整包中的只读模型目录。容器仅用于 CI 测试，UOS 目标机无需 Docker 或 root。

以下为 CI 中一页简短扫描 PDF（`CPU OCR 1234.56`）的耗时，不能用于估计复杂 A4 文档或兆芯 CPU 的速度。所有结果均包含正确的 `CPU OCR 1234.56`，WebUI HTTP 状态为 200。

| CI 环境与包 | Standard | Advanced |
| --- | ---: | ---: |
| Windows Full | 39.20 秒 | 97.42 秒 |
| Windows Runtime，外部模型 | 6.48 秒 | 未运行 |
| Linux Full，禁网普通用户 | 7.37 秒 | 93.44 秒 |
| Linux Runtime，外部只读模型 | 6.37 秒 | 未运行 |

本地 AMD Ryzen 5 5600X 的四行中文、英文、数字扫描 PDF 测试中，Standard 约 19 秒、Advanced 约 74 秒；姓名、编号、金额均正确。最终 Windows Runtime ZIP 解压到中文空格目录后，外部模型 OCR 也已通过。

## 下载与报告

在上述运行底部的 Artifacts 下载：

- `mineru-windows`：Windows 的 Full、Custom、Runtime 三个 ZIP。
- `mineru-linux`：UOS20/Linux 的 Full、Custom、Runtime 三个 tar.gz。
- `build-and-windows-reports`：逐文件校验、ELF 审计、Windows 推理结果及 `SHA256SUMS.txt`。
- `linux-test-reports`：Linux 推理结果与服务日志。

下载 Actions 产物需要登录 GitHub。包保留 7 天，报告保留 14 天；过期后可手动重新运行工作流。模型缓存独立于程序依赖锁，缓存仍存在时可复用；本地 Runtime 外部模型部署不依赖云端缓存。

## DOCX 导出基准

长文档导出曾慢到看起来像卡住：580 页 basic 档位解析完成后，导出 DOCX 长时间停在“准备中…”。
渲染耗时无法用一页扫描 PDF 体现，所以工作流新增独立任务 `export-bench`：只安装固定版本的
docvortex 0.5.16 与 python-docx 1.2.0，不下载模型、不做推理，用同一份 580 页合成 Middle JSON
（正文、标题、列表、跨页续文、表格、行内与行间公式、复用图片）对比补丁前后的导出耗时。

- `app/packages/mineru_docx_fast.py` 去掉四处纯浪费：整份文档的深拷贝、重复公式的
  LaTeX→OMML 转换、重复图片的 PIL 解码、DOCX 内图片的二次 zlib 压缩。
- 基准要求两次产物都能被 python-docx 打开，且段落数、内联图片数、正文长度一致，
  包内图片成员按补丁原样存放（`ZIP_STORED`）；加速不足 5% 或补丁后仍超过 300 秒即判失败。
- 真实包上的端到端验证在 `smoke_portable.py`：解析完成后从兼容页导出 DOCX，校验文件结构、
  可读文本与图片存放方式，并记录耗时。

实测（运行 41690124825，提交 `43f3d96`；580 页合成文档、13781 个顶层块、116 张表格、97 处图片引用）：基线 31.36 秒 → 补丁后 10.39 秒，约 3.02 倍；热缓存 10.56 秒。产物体积 14 747 751 → 14 778 438 字节（+0.2%），段落数、表格数、内联图片数与正文长度逐项一致，补丁四项（公式缓存、图片缓存、样式缓存、包压缩）全部生效。

结果见 Actions 任务 `export-bench` 与产物 `docx-export-bench`（保留 14 天）。

真实包上的端到端导出验证放在 `smoke_portable.py`：解析完成后从兼容页请求 DOCX，校验文件结构、可读文本与图片成员的存放方式（图片成员必须按补丁原样存放）。运行 41662774726 的结果如下；烟雾用的两页扫描件识别结果里没有图片块，因此媒体成员数为 0。

| 包 | 兼容页解析 | DOCX 导出 | 产物大小 |
| --- | ---: | ---: | ---: |
| Windows Full | 8.78 秒 | 0.33 秒 | 37 KB |
| Windows Runtime，外部模型 | 8.81 秒 | 0.33 秒 | 37 KB |
| Linux Full，禁网普通用户 | 8.05 秒 | 0.21 秒 | 37 KB |
| Linux Runtime，外部只读模型 | 8.05 秒 | 0.20 秒 | 37 KB |

同一次运行的识别耗时：Windows Full Standard 11.70 秒、Advanced 235.70 秒；Windows Runtime（外部模型）Standard 12.08 秒；Linux Full Standard 10.68 秒、Advanced 240.93 秒；Linux Runtime（外部只读模型）Standard 10.54 秒。

## 未验证范围

尚未在真实 UOS20/兆芯 KX-7000 上运行。容器测试验证了 glibc 2.28、无 root、禁网和只读模型条件，不代表特定硬件指令集已经验证。目标机应先执行 `bash start.sh --check`，再使用代表性文档测试。

程序与模型可分开更新，但 MinerU 新版本可能改变模型文件、格式或接口。只有新版本自检和推理测试通过后，才能确认原模型继续兼容。
