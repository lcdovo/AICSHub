# M5原生注意力推理候选对照

2026-11-04。已核实本地镜像支持并实际加载原生SDPA/FP16，完成原实现→原生注意力→恢复原实现的两组有限对照，分别使用batch4与batch8。有效两组共1200条正式请求全部完成、BUSY0；原生候选并发10重排P95分别2058.153ms和2138.241ms，均超过原2000ms门槛。候选不采用，业务默认process及模型路由不变；原容器恢复后与临时容器均已停止，数据卷/权重缓存保留。

## 实现与运行核实

只使用现有本地Infinity镜像`sha256:64873a491e839963036d1171122695e3eb5780addb931a6b1df01dbf7bee921d`，固定模型BAAI/bge-reranker-v2-m3、CUDA、FP16、无compile、无启动最大批预热；通过`--no-bettertransformer`改变注意力实现，第二组另将batch4改为8。模型权重缓存只读、HF/Transformers离线环境保持；未下载/转换/量化或改候选/Token截断。

本地镜像源码核对：PyTorch2.8.0+cu128、Transformers4.49.0.dev0，Infinity0.0.77。crossencoder/torch.py在BetterTransformer路径先选eager再转换，关闭转换后由Transformers加载原生实现。实际候选通过只读挂载的`runtime_attention_probe/sitecustomize.py`在模型初始化完成后打印固定元数据，核实`attention_implementation=sdpa`、`dtype=torch.float16`、`XLMRobertaSdpaSelfAttention`；不以CLI旗标或健康响应代替实际实现证据。该探针仅挂载到本轮临时容器，不修改镜像或业务容器。

新增`scripts/compare_attention_pressure.py`与`tests/test_attention_pressure.py`；复用并扩展`compare_gpu_batch.py`的严格命令/只读挂载/条件恢复工具。`verify_gpu_pair.py`逐样本新增应用返回的四位小数rerank_score，保存完整20个索引及分值以对照；这些不是原始logits。相同查询的每个候选分值与排名分别核对，缺项/重复/NaN/不同workload/错误次序不形成比较结论。

## 自动化验证

最终78项相关T0通过，0失败/错误/跳过，0.75秒；报告`reports/offline/20261104-203658-757209`。包括原命令保护、原生/组合候选仅改变指定旗标、缺失分值/错数据拒绝、排序变化与分值差保留、实际SDPA/精度/类名核验、中文路径UTF-8及既有资源恢复/端点/批次边界。compileall app scripts、切片Ruff F401/F821及git diff --check通过。

最终源码指纹`fd8b1401a136bd70604a1f51e3eab980922a498ed07e732570b30056728ab051`，该T0与batch8组合整组同源，起止稳定完整、blocked_network_operations=0。batch4单变量组指纹`c782bfa8f9ba9c61efc42b39fd305e8c5a7bac09d787aaa628e5d28c700f6d82`也稳定完整，最终只增加batch8组合参数与相关保护断言，分版本解释。此片未改生产Python、未重跑全项目T0，前片1105通过不能冒称本片新全量。

## 真实有限对照

命令：`python scripts/compare_attention_pressure.py`；组合命令追加`--native-batch-size 8`。每组3轮，每轮原200份负载、串行100与并发10的100条，20个完整候选、12重复语义簇；工作负载hash均`188302e1a056e46cd3300aea9959a8c3d3356890b987bf67a6d5a00802fd5393`。保留10个整轮资格、Embedding微批及2个重排出口，不放宽原截止与延迟门槛，不调用聊天/Judge或付费供应商。

| 组别 | 配置 | 完成 | 并发重排P95（含排队） | 并发总P95 |
| --- | --- | ---: | ---: | ---: |
| 单变量A1 | 原实现batch4 | 200/200 | 2770.125ms | 2806.225ms |
| 单变量B | SDPA batch4 | 200/200 | 2058.153ms | 2112.187ms |
| 单变量A2 | 恢复原实现batch4 | 200/200 | 3515.372ms | 3560.819ms |
| 组合A1 | 原实现batch4 | 200/200 | 2895.651ms | 2937.882ms |
| 组合B | SDPA batch8 | 200/200 | 2138.241ms | 2190.971ms |
| 组合A2 | 恢复原实现batch4 | 200/200 | 2815.552ms | 2854.015ms |

两个总报告为`reports/gpu-pair/attention-pressure-20261104-203237/report.json`与`attention-pressure-20261104-203702/report.json`；三轮子报告均由总报告精确指向，保存源码、配置/模型/容器、逐样本、GPU采样。两组均DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE，非性能门槛PASS。不同运行时间、非独占宿主与采样粒度限制保留，不根据一次更快就宣称稳定提升或长期QPS。

## 排序及数值边界

两组原生候选相对前后原实现的top1一致197/200、完整排序一致111/200，应用四位小数分值最大绝对差0.0106；前后原实现200/200完整排序一致、分值差0。单变量3个top1变更为load-151的0→12、load-159与load-173的12→0；剥离合成编号前缀后正文完全相同。该有限负载中未发现这3个top1变化涉及不同正文，但不能把重复正文负载当独立质量留出，更不能据此批准不同模型/Embedding空间或保证一般查询等价。

## 失败与恢复

启动后曾中止`attention-pressure-20261104-202924`未完成运行，以在压力前而非压力后捕获实际注意力启动标记，避免日志tail被202次HTTP覆盖；报告目录和原子报告保留，不计有效对照。

`attention-pressure-20261104-203017/report.json`保留失败：原实现首轮已完成，临时候选inspect含中文宿主挂载路径，Windows默认GBK解码失败，清理时重复inspect失败，临时容器未自动停止而原容器已恢复。已按本轮owner与ID核实后手动停止临时容器，无业务资源删除。随后Docker输出统一UTF-8，临时容器按创建得到的不可变ID先stop、后inspect最终状态，修复“inspect失败导致跳过stop”；真实复验两次temporary_final_state=exited、original_restored_healthy=true。早期失败/中止消耗的本地请求不隐藏，也不当作有效1200条的一部分。

## 决策与下一范围

不启用原生SDPA或组合batch8，保持业务process及原batch4。现有有限候选仍未达并发10/2000ms，质量独立留出也未完成。已排除只靠入口微批、单纯batch大小和此原生注意力替换即可稳定过门槛的假设；后续应先冻结容量预算及推理后端候选（例如现有本地ONNX/TensorRT链的可用性、权重转换与数值验证），再决定投入，不继续堆加队列/截止或反复热身挑最好成绩。

真实业务bge-reranker-large、后台索引竞争、多worker、长期稳定性、完整REST/WS/检索链仍未验；M4实体95.67%与M5人工595条待复核、付费660/660状态不变。
