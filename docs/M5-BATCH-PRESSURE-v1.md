# M5微批准入下的batch4/8/4压力对照

2026-11-04。结论：三轮各200条全部完成，batch8降低有限样本延迟，但并发10重排P95仍2374.700ms，高于原2000ms门槛，性能保持FAIL。原batch4恢复，两个模型候选与临时batch8容器均已停止，业务默认process/模型/知识库未切换。M4实体与M5独立人工质量门禁、供应商660/660上限不变。

## 本片改动

- `scripts/verify_gpu_pair.py`允许显式选择已核实的临时loopback重排候选；临时端点仅限scheduled/shared-services/skip-compatibility，防止实际HTTP仍访问固定7999而测试错服务。生产默认、原端点与原负载均保留。
- 新增`scripts/compare_batch_pressure.py`：复用原模型容器及只读HF缓存的隔离batch8工具，执行batch4-A1→batch8-B→batch4-A2。每组均使用单进程batch候选、200份原负载、每份20候选、串行100/并发10的100条；所有失败和超时计入，任意未完成/源码异常中止后续加压，不重试失败样本。每次子进程最多240秒。
- 在临时容器上下文的finally中停止临时容器、按原ID恢复原batch4并核对健康；父进程最终把原两个模型容器恢复到本次之前的停止状态。未创建/删除知识collection、数据库或数据卷，没有付费/聊天/Judge请求。
- 此片仅调整实验工具及测试，无生产Python行为变化。配置、候选实现及之前未提交修改保持原状。

## T0与来源

`python scripts/test_offline.py -q tests/test_batch_pressure.py tests/test_gpu_arrivals.py tests/test_gpu_pair_acceptance.py tests/test_gpu_capacity_diagnostics.py tests/test_local_batching.py`：63通过、0失败/错误/跳过，0.64秒。报告`reports/offline/20261104-200552-065329`；包含10项新对照完整性/临时端点实际派发测试及既有候选生命周期/有界隔离回归。

覆盖：缺组、重复样本、BUSY/失败、工作负载hash变化、非法延迟或次序均拒绝形成结论；2000ms门槛保留且不自动批准业务切换。实际使用临时端口创建重排服务，生命周期仍由创建者关闭。`python -m compileall -q app scripts`、切片Ruff F401/F821、`git diff --check`通过。本片未重跑全项目；前片1105通过为前片完整T0证据，不冒称本片新全量。

真实执行：`python scripts/compare_batch_pressure.py`。总报告`reports/gpu-pair/batch-pressure-20261104-200603/report.json`，状态DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE。T0、总报告和三轮源码指纹均`8c20782bbca97775cd0ea9fa4ae08052ac0bf8d410cdf376915b3532eabda71e`，起止稳定完整。三轮工作负载hash均`188302e1a056e46cd3300aea9959a8c3d3356890b987bf67a6d5a00802fd5393`。

## 真实结果

| 配置 | 串行完成 | 串行重排P95 | 并发10完成 | 并发重排P95（含排队） | 并发总P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| batch4 A1 | 100/100 | 312.334ms | 100/100 | 2807.103ms | 2861.192ms |
| batch8 B | 100/100 | 280.272ms | 100/100 | 2374.700ms | 2413.609ms |
| 恢复batch4 A2 | 100/100 | 308.736ms | 100/100 | 2910.699ms | 2946.822ms |

三轮BUSY/请求失败均0；阶段门槛三轮均FAIL。batch8比前后batch4的并发重排P95分别低约15.4%/18.4%，仍不能以该单次A/B/A推断统计显著性或保证长期性能。三个子报告分别为：

- `reports/gpu-pair/acceptance-20261104-200622/report.json`
- `reports/gpu-pair/acceptance-20261104-200749/report.json`
- `reports/gpu-pair/acceptance-20261104-200910/report.json`

三轮相同case的完整20候选排名均200/200一致，top1同样一致；这仅核对12重复语义簇的有限合成负载，不证明独立质量/真实业务bge-reranker-large兼容或分值等价。采样GPU总显存峰值5011/5020/4989MiB，不等于精确瞬时峰值或独占模型显存。

## 决策与下一范围

不采用batch8作为业务配置，不修改原并发10/20候选/2000ms门槛，不增大等待截止来制造通过。当前应用排队与GPU吞吐仍制约原目标；本次证据只说明调大服务批次有有限收益但不够。

下一候选需要针对推理本身实施可回滚优化，例如核实现有本地权重和运行时支持的推理后端/数值精度，再做同负载与排名/分值一致性对照；或者设计保留逐请求query/document绑定的服务端跨查询调度。后台索引竞争、长时稳定性、多worker与完整API链仍待验，不能继续只调入口队列或增加等待预算。
