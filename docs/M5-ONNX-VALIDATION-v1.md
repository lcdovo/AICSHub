# M5 ONNX候选验证结果

2026-11-05最新进度：显示器、扫地机两组各6题已由用户明确批准，累计12/595；其余583题待审。裁决按原材料hash分别保存。整体独立留出、全局语义隔离及实际输出论断尚未通过。

2026-11-04执行，2026-11-05记录。GPU运行时与FP16模型导出已完成，实际模型数值对照和原200条模型直调诊断完成；并发10 P95=2800.145ms仍超过2000ms，性能未通过，不启用业务候选，M5仍未完成。

## 构建与隔离

官方PyPI元数据固定onnxruntime-gpu1.19.2、cp310/Linux x86_64 wheel，226178508字节，SHA256 `a49740e079e7c5215830d30cde3df792e903df007aa0b0fd7aa797937061b27a`。官方源直接下载先触发360秒总构建超时，原报告`reports/gpu-pair/onnx-build-20261104-234158/report.json`保留。随后从清华镜像取同文件，大小/官方SHA256一致；原始下载来源、元数据和日志在`reports/onnx-wheel-cache/`。构建使用--network=none、--no-index、--no-deps，镜像不替换业务镜像。

可重复入口：`python scripts/build_onnx_candidate.py --wheel <verified-local-wheel>`。错误文件名/缺文件/错哈希均在docker操作前拒绝。最终构建报告`reports/gpu-pair/onnx-build-20261104-235128/report.json`，镜像ID `sha256:8c9f2fceea59eacbd809fef0a90897bf2441bf65e646bfebdcd9516319001a4a`。

阶段入口`verify_onnx_candidate.py --build-report <report.json> --stage smoke|export|compare`。容器无网络、不发布端口、原权重和脚本只读，仅独立输出目录可写；内存/内存加swap均5GiB、CPU4、tmp512MiB，导出另检查CPU/GPU内存前置。最大等待600秒，结束按本轮创建的不可变ID停止，随后检查最终状态与原候选ID/状态。

## 实际分层结果

- 小矩阵：`reports/gpu-pair/onnx-smoke-20261104-235159/report.json`，禁用CPU节点回退的CUDA MatMul值校验通过；只证明小图。
- 导出：`reports/gpu-pair/onnx-export-20261104-235204/report.json`，只读固定bge-reranker-v2-m3 revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`，FP16 CUDA导出约61.4秒，model.onnx1136106005字节、SHA256 `9fe6ee838e68d6263269e3da738cde9832bcc86378563fb0b5dcc99797739fb3`。完整产物hash在export.json，导出状态仍EXPORTED_NOT_VALIDATED直到后续独立对照。
- 数值：12条原开发簇查询、每条20完整候选，以eager FP16 PyTorch对照，冻结容差atol0.02/rtol0.01，12/12满足；最大原始logit绝对差0.01611328125。top1一致12/12、完整排序9/12，不能据此声称一般质量或严格排序等价。
- 实际执行profile：56400条CUDA kernel事件；CPU包含11040条有界整数形状运算和1440条单元素整数→浮点形状转换。审计拒绝CPU浮点张量模型计算、超界元数据、未知provider及无CUDA证据。该观察仅覆盖12条开发查询；不是所有节点纯GPU，更不是任意动态输入保证。

首轮`onnx-compare-20261104-235333`禁止所有CPU节点，初始化失败；第二轮`onnx-compare-20261104-235526`已完成12条数值比较，但审计未识别形状整数→单浮点标量转换而中止加压。两份失败、原profile与CPU事件清单保留。之后仅明确允许该单标量转换并补拒绝浮点输入/多元素转换的测试；没有降低2秒门槛。运行时仍调用disable_fallback禁止执行器错误后的自动降级。

## 原负载直调诊断

最终报告`reports/gpu-pair/onnx-compare-20261104-235658/report.json`和同目录comparison.json，DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE。原200条输入/12重复语义簇、每条20候选、分批4、tokenizer不截断且超过512即拒绝；串行100与并发10的100条，不重跑失败样本。数值审计阶段开启profile；加压前结束profile并释放PyTorch对照模型。没有Embedding/HTTP/provider/应用排队，因此不是前一完整provider链的直接A/B。

| 并发 | 完成 | 模型直调P95（含tokenizer/分批） | 原2000ms门槛 |
| --- | ---: | ---: | --- |
| 1 | 100/100 | 267.610ms | 有限直调满足 |
| 10 | 100/100 | 2800.145ms | FAIL |

所有attempt保留在分母；>5秒记deadline，异常不当成功。没有独立语义留出、后台竞争、长时、多worker或REST/WS验收。不采用ONNX候选，不对HTTP端点进行业务接入。

## 自动化与证据边界

90项相关T0通过（0失败/错误/跳过，0.84秒），`reports/offline/20261104-235658-607845`；compileall app scripts及本片Ruff F401/F821通过。新增校验覆盖wheel完整性、build来源、内存前置、产物路径/大小/哈希、离线只读资源、缺项/非有限分值、排名变化、所有失败计分、CPU模型计算/元数据边界。T0与最终真实对照source指纹同为`6addad096c7848f45eca795add452d3f99f58b0d7da0161d99d4c3663fd550ce`，起止稳定完整、禁网络操作0。这是本片代码版本；随后查询改写状态切片另有证据，不能覆盖引用同一指纹。

本轮真实导出/推理使用本地合成输入，付费供应商新增0，额度660/660保持。候选最终均exited、无OOM，原候选及业务服务保持停止；没有操作业务库、知识collection、卷或原权重。M4实体95.67%与M5人工/完整质量/性能门禁不变。人工进度现单列为显示器与扫地机共12题通过、其余583待审。
