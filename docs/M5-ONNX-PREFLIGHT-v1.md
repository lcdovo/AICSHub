# M5 ONNX/GPU运行时可行性预检

2026-11-05最新进度：显示器、扫地机两组各6题已由用户明确批准，累计12/595；其余583题待审。裁决按原材料hash分别保存。整体独立留出、全局语义隔离及实际输出论断尚未通过。

2026-11-05更新：后续GPU ORT镜像、小图及FP16模型导出已成功；12条数值对照通过、top1一致12/12但完整排序9/12。原200条ONNX直调全部完成，并发10 P95=2800.145ms仍超2000ms，不采用候选；CPU形状/标量运算及失败记录详见M5-ONNX-VALIDATION-v1.md。人工复核显示器与扫地机共12题已通过，其余583题待审，整体留出/输出论断仍未通过。以下历史记录保留。

2026-11-04。当前结论BLOCKED_GPU_RUNTIME：本地Infinity镜像只有CPU版ONNX Runtime，无CUDA执行器、无TensorRT。GPU版派生镜像安装包未下载完成，尚未构建成功。没有执行ONNX模型导出、ONNX小图推理、实际模型GPU推理、数值/排序比较或压力测试；全部保留NOT_RUN，不把CPU fallback、torch GPU成功或包能导入当实际ONNX模型通过。M5容量/质量仍未通过，业务默认与原服务停止状态保持。

## 实际预检

运行`python scripts/preflight_onnx_runtime.py`，核对原gpu-model-switch容器owner、固定镜像ID和权重卷；临时容器使用--network none、--read-only、256MiB临时/tmp、只读权重与只读脚本、禁止HF下载及遥测。不启动原模型容器、不修改权重，不清理业务数据。

最终报告`reports/gpu-pair/onnx-preflight-20261104-231059/report.json`，probe_exit_code=0但整体BLOCKED_GPU_RUNTIME、pressure_status=NOT_RUN，退出码2符合未就绪状态。原始预检`onnx-preflight-20261104-230312`也保留。

| 项目 | 实际观察 |
| --- | --- |
| 固定镜像 | sha256:64873a491e839963036d1171122695e3eb5780addb931a6b1df01dbf7bee921d |
| GPU | RTX4060 Laptop、8GiB、compute capability8.9 |
| PyTorch | 2.8.0、CUDA12.8，GPU小矩阵乘实际成功 |
| ONNX Runtime | 1.19.2，只有AzureExecutionProvider/CPUExecutionProvider，无CUDA库 |
| onnxruntime-gpu / TensorRT | 未安装；TensorRT模块缺失 |
| 导出依赖 | ONNX1.17.0、Optimum1.24.0、exporters.onnx可导入，仅前置条件 |
| 本地权重 | bge-reranker-v2-m3，revision953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e，XLMRobertaForSequenceClassification，safetensors2271071852字节 |
| 本地ONNX/whl缓存 | 无 |
| 原候选状态 | exited，预检前后ID/状态一致 |

配置与文件清单不证明所有权重文件均独立hash校验或已成功导出；本片只对config保存hash。torch GPU小图成功不说明ORT GPU或实际模型成功。磁盘主机C约63GB/D约191GB可用、Docker虚拟文件系统约937GB可用仅为各自观察口径；未以虚拟盘容量替代宿主机可用空间。主机当次可用物理内存约2GB，Docker总内存约8.2GB，2.27GB权重的导出/副本开销未验证，暂不启动重型导出。

## 独立运行时安装尝试

新增`scripts/onnx_candidate/Dockerfile`，仅派生实验镜像，卸载其CPU ORT并安装同版本onnxruntime-gpu1.19.2（PyPI官方索引、--no-deps、无原业务镜像/宿主Python改动）。TensorRT不安装，不把大规模新依赖默认加入业务。

首个FROM sha256:本地ID的构建被BuildKit当作远端库镜像名解析，远端连接失败；之后核实原ID并创建本地标签aicshub-onnx-base:pinned-64873a491e83，--pull=false构建可读取本地基镜像。GPU wheel开始下载，226.2MB包持续数分钟没有完成；约23:04:33开始，约23:10停止，退出1。最后docker image inspect确认aicshub-onnx-candidate:ort-1.19.2不存在，Docker build相关进程已退出，没有遗留运行候选。该次是人工中止，不伪记网络超时或构建成功；控制台输出为当次执行记录，没有声称已有build_onnx_candidate结构化报告。

随后提供`python scripts/build_onnx_candidate.py`可重试入口：核实原固定ID、建立本地基标签、总构建时限360秒、pip读超时30秒/重试0、构建日志与源码起止清单独立保存。此入口本片仅做失败/超时替身测试，未再自动发起同一大文件下载。基础本地标签保留，不影响原标签；未执行全局prune或数据清理。

## 分层验证工具

- `runtime_onnx_probe.py`：在原固定镜像断网/只读检查版本、providers、GPU、模型快照；已执行。
- `onnx_candidate/cuda_smoke.py`：待GPU运行时可用后，使用ORT MatMul小图并禁用CPU fallback、核对CUDA provider与值；未执行，不能算GPU ORT已可用。
- `onnx_candidate/export_model.py`：指向固定本地快照，只能输出新建/onnx-output/model；禁网络、不覆盖已有目录，保存导出文件hash与EXPORTED_NOT_VALIDATED状态。未执行；do_validation=False明确不跳称数值验证通过。实际导出仍须先解决内存条件。
- 不自动建立业务端点或调整业务模型；得到导出产物后还需实际ORT GPU载入、至少完整20候选的分值/排序比较，最后才运行原200负载。

## T0与未验范围

94项相关T0通过，0失败/错误/跳过，0.86秒，报告`reports/offline/20261104-231046-455708`。新增16项检查缺CUDA/缺导出依赖/缺权重/架构不符、空观测、TensorRT包与provider区别、严格只读断网命令、owner/镜像/缓存拒绝、构建失败/360秒超时不产生可用结论。compileall app scripts、切片Ruff F401/F821、git diff --check通过；导出与smoke仅语法检查，不冒称实际执行。

最终T0与最终真实预检源码指纹同为0e99103dcd74a3359d7f31630f7e624c63a09aface47fd92833c1bd8d5350125，起止稳定、快照完整，T0 blocked_network_operations=0。此片没有生产Python修改、未重跑完整项目；前片1105仅历史全量。

下一步先完成隔离GPU运行时构建与小图验证，并为导出提供足够内存，再导出/验证；若环境条件暂不可满足，停止重复压力调参，转向M5质量留出复核。595条候选仍待独立人工复核，供应商660/660不变。未运行真实聊天模型、未调用付费评测、未动业务数据，所有AICSHub服务保持停止。
