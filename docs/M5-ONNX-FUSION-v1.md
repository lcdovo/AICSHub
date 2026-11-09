# M5 ONNX部分图融合对照

2026-11-05。本片仅新增本地隔离实验工具，不修改业务模型、路由、等待预算或2秒门槛。已存在的未提交查询改写、批处理及原方案文件保留。

## 前置和所有权

固定GPU ORT镜像sha256:8c9f2fceea59eacbd809fef0a90897bf2441bf65e646bfebdcd9516319001a4a，原FP16 ONNX为reports/gpu-pair/onnx-export-20261104-235204。原权重与ONNX只读，临时候选独立目录，不启动业务栈。bge-reranker-large未删除：共享权重卷保留约2239618772字节的原safetensors，当前.env仍引用该业务回退模型；服务停止时缓存只占磁盘，不据此声称占GPU显存。

## 失败与调整

- onnx-optimize-20261105-001829：5GiB容器上限内OOMKilled=true、exit137，没有有效候选。
- onnx-optimize-20261105-002012：关闭可选符号形状推断后仍OOM，失败保留；不通过放宽资源上限掩盖。
- onnx-optimize-20261105-002207：独立子进程把权重保存为external data后，图结构小幅加载，在原上限内完成；BiasGelu24，Attention/MultiHeadAttention0/24，不能说注意力融合成功。
- onnx-optimize-20261105-002325：权重外置后启用符号形状推断，完成BiasGelu24、SkipLayerNormalization48、LayerNormalization1，Attention/MultiHeadAttention仍0/24，状态PARTIAL_FUSION_NOT_ACCEPTED。

不启用GELU近似、量化、token截断或静态batch。嵌入层融合关闭，模型权重/revision、动态batch/sequence输入保留。生成源图、独立外部权重与新图的每文件hash写入export.json；check_export逐项校验后才进入对照。

## 工具和验证

scripts/onnx_candidate/optimize_model.py保存实际算子数量与资源失败；verify_onnx_candidate.py追加optimize阶段，继续无网络、只读源/权重、内存5GiB/总内存加swap5GiB、CPU4、最多600秒，结束按新建不可变ID停止。scripts/compare_onnx_fusion.py顺序运行原版A1→部分融合B→原版A2，每轮调用同一compare_model.py完成12条数值/完整20候选/profile审计，再执行原200条直调负载。相同源指纹与workload、不删失败/忙拒绝/超时；三轮不完整则停止加压、保留已运行证据。

72项相关T0通过，0失败/错误/跳过（0.67秒），reports/offline/20261105-002530-216360，source指纹8211e627e72d6736af82f9e2bbd69e828596bc83319785e7cca7f12850e5cbfc，起止稳定、快照完整、blocked_network_operations=0。包括15项融合架构/数量/资源保护和11项ABA完整性检查。compileall app scripts、本片Ruff F401/F821、git diff --check通过。未重跑完整项目；之前1195全量为前片版本，不能冒称本片新全量。

## 最终结论

最终总报告`reports/gpu-pair/onnx-fusion-aba-20261105-002530/report.json`为FAIL。原版A1完成200/200，串行P95=305.759ms、并发10 P95=2875.694ms；它仍未过原2000ms门槛。

候选B实际模型profile符合既有CPU元数据/CUDA计算边界，但12条数值对照仅11条通过。load-003最大原始logit差0.0390625，超过原atol0.02/rtol0.01容差；top1仍12/12一致、完整排序4/12一致，不能以首位相同忽略数值失败。候选子报告`reports/gpu-pair/onnx-compare-20261105-002718/report.json`，退出1、错误onnx_numeric_tolerance_failed。逐候选完整logits/profile保留在同目录comparison.json。

按预置边界停止后续加压：候选200条压力NOT_RUN、原版A2 NOT_RUN；不补跑、不扩大容差，不声称完成ABA或有性能收益。没有启用HTTP/业务端点，也没有替换原模型、输出图或权重。候选与原版A1临时容器均exited、original_unchanged=true，最终无OOM（早期两个融合OOM另存，未删除）。

最终T0与本轮ABA/子报告源码指纹均8211e627e72d6736af82f9e2bbd69e828596bc83319785e7cca7f12850e5cbfc，起止稳定完整；实际完成不等于完整M5验收。

## 固定边界

200条仍是12重复语义簇的有限合成输入，每条20候选，含tokenizer/分批的模型直调，不包含HTTP、Embedding、应用排队、REST/WS、多worker、后台索引竞争或长时稳定性。独立人工质量集、M4实体95.67%门槛及M5整体状态不因融合或吞吐改善而升级。付费供应商新增0，660/660保持；人工已批准12/595，投影仪6题仍待用户裁决。
