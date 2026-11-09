# M5 当前文件与证据清单

ONNX预检：新增scripts/preflight_onnx_runtime.py、runtime_onnx_probe.py、build_onnx_candidate.py、onnx_candidate/Dockerfile及两份待执行验证/导出脚本，新增tests/test_onnx_preflight.py、docs/M5-ONNX-PREFLIGHT-v1.md。94相关T0通过，原运行时缺GPU ORT，派生镜像未构建完成，导出/真实ORT推理/压力NOT_RUN；生产Python与业务配置不变。

原生注意力实验：新增scripts/compare_attention_pressure.py、scripts/runtime_attention_probe/sitecustomize.py、tests/test_attention_pressure.py、docs/M5-ATTENTION-PRESSURE-v1.md；扩展compare_gpu_batch.py、verify_gpu_pair.py并修正evaluate_gpu_arrivals.py临时inspect的UTF-8。78相关T0通过，两个ABA候选性能仍FAIL，生产Python/业务配置本片未改。

微批准入下batch压力对照：新增scripts/compare_batch_pressure.py、tests/test_batch_pressure.py、docs/M5-BATCH-PRESSURE-v1.md，verify_gpu_pair.py增加受控临时端点。63相关T0通过；三轮各200条全部完成但性能均FAIL，batch8不采用，原容器恢复后停止。此片无生产Python/业务配置变化。

跨请求候选：新增app/services/local_batching.py、tests/test_local_batching.py、docs/M5-CROSS-REQUEST-BATCH-v1.md；修改local_admission.py、embedding_service.py、config.py、.env.example及verify_gpu_pair.py。候选默认关闭，102项相关回归通过，真实200请求全部完成但并发重排P95超过原门槛，整体FAIL；没有前端变化或业务切换。

固定到达率/batch候选切片：新增scripts/evaluate_gpu_arrivals.py、scripts/compare_gpu_batch.py、tests/test_gpu_arrivals.py、docs/GPU-ARRIVALS-BATCH-v1.md。包含生成器有界在途/迟到/失败计数、只读离线候选batch8及finally恢复batch4。61相关T0通过，真实A/B/A仍有过载拒绝，业务/生产Python未改。

常驻容量/Embedding生命周期切片：scripts/verify_gpu_pair.py新增--shared-services，新增scripts/diagnose_gpu_capacity.py、tests/test_gpu_capacity_diagnostics.py、docs/GPU-CAPACITY-DIAGNOSIS-v1.md；后续app/services/embedding_service.py补池生命周期/本地代理/截止，新增tests/test_embedding_http_lifecycle.py、docs/EMBEDDING-HTTP-LIFECYCLE-v1.md。157相关回归通过，真实常驻200串行通过、并发容量FAIL，未切业务。

后续释放补偿切片：修改app/services/local_admission.py、scripts/diagnose_local_admission.py、scripts/test_local_admission_redis.py、tests/test_local_admission_diagnostics.py；新增tests/test_local_release.py和docs/LOCAL-LEASE-RELEASE-v1.md。135项相关T0与11项真实Redis通过；模型性能与质量仍按各自门禁记录，未切业务。

以下为本次M5新增/修改的主题文件；整体仍未完成。

| 主题 | 文件 |
| --- | --- |
| 冻结标准 | docs/M5-PLAN-v1.md、docs/M5-GATES-v1.json |
| 发布底线 | app/agent/publication_guard.py、app/agent/nodes/response_node.py、tests/test_m5_workflow_guard.py |
| Judge/证据 | app/agent/validation_llm.py、tests/test_m5_judge.py、tests/test_m4_generation_metadata.py |
| 固定事实 | app/agent/critical_facts.py、app/agent/validation.py、tests/test_m5_critical_facts.py、tests/test_chat_turns.py |
| 引用身份 | app/agent/citation_guard.py、tests/test_m5_citations.py |
| 数据/指标 | scripts/evaluate_m5.py、tests/test_m5_metrics.py、acceptance/m5/ |
| 本地消融 | scripts/evaluate_m5_local.py、app/services/knowledge_base/versioned_search.py、tests/test_m5_ablation.py |
| 过程证据 | docs/M5-EVIDENCE-v1.md、docs/AGENT_GRAPH.md |
| 待人工材料 | docs/M5-REVIEW-QUEUE-v1.md、docs/M5-HOLDOUT-*-v1.json、docs/M5-HOLDOUT-CORPUS-REVIEW-v1.md、docs/m5-review/batch-01～12.md |

原方案/验收规范和scripts/_dev_run_import.py继续保持原未跟踪状态；不夹带提交。reports下报告被Git忽略，本地路径由证据文档记录，不提交数据库/密钥/日志/构建产物。

最终验收汇总：docs/M5-ACCEPTANCE-v1.md；全局195项索引同步M5局部证据，未标整体通过。595候选为助手草拟，只通过程序完整性，待用户语义/事实复核。网关截止测试调整在tests/test_m4_gateway.py，退款恢复审查在app/services/run_execution.py及tests/test_m2_runs.py；均有独立提交和保留失败证据。

后续重排预算切片：修改app/services/reranker_service.py、app/services/knowledge_base/versioned_search.py、scripts/evaluate_m5_local.py、tests/test_reranker.py；新增tests/test_m5_rerank_budget.py、docs/M5-RERANK-BUDGET-v1.md，更新验收证据/门禁/本文件及全局JSON索引。功能107项通过，实际5秒性能8/12local、4超时回退，仍未验收通过。

资源对照切片：scripts/evaluate_m5_local.py隔离loopback端点；tests/test_m5_local_endpoint.py九项边界测试；更新M5设备对照、证据、验收门禁和本清单。业务生产代码与部署未修改。

双GPU候选切片：新增compose.gpu-candidates.yaml与docs/GPU-PAIR-DEPLOYMENT-v1.md；修改scripts/evaluate_m5_local.py、tests/test_m5_local_endpoint.py、M5证据/门禁/清单。候选已运行，业务路由未切换；原文件和数据保留。

双GPU压力与兼容切片：新增scripts/verify_gpu_pair.py、tests/test_gpu_pair_acceptance.py、docs/GPU-PAIR-ACCEPTANCE-v1.md；更新候选部署记录、M5验收/门禁/证据及清单、全项目JSON索引。压力结果FAIL、业务保持，75项受影响T0通过。

共享预算/背压切片：新增app/services/local_inference.py、tests/test_local_inference.py、docs/LOCAL-INFERENCE-BUDGET-v1.md；修改Embedding/Reranker出口、knowledge_base/versioned_search.py及index_worker.py、agent/nodes/rag_node.py、scripts/verify_gpu_pair.py和相关架构/API/部署验收记录。单进程功能验证通过，高并发完成率仍失败，未切GPU业务路由。

整轮/跨worker候选切片：新增app/services/local_admission.py、scripts/test_local_admission_redis.py、tests/test_local_admission.py、docs/WHOLE-REQUEST-ADMISSION-v1.md；修改app/config/config.py、.env.example、docker-compose.yml透传可选process/redis模式及namespace；Embedding/Reranker/知识检索和索引Job接入整轮资格，旧BGE线程继承context；scripts/verify_gpu_pair.py增加whole-request/redis隔离压力，tests/test_local_inference.py保留阻断断言并替换新入口；更新架构/API/M5门禁/全局JSON索引。配置未应用业务，1038全量/7真实Redis通过，实际模型压力FAIL。

2026-11-05追加：build_onnx_candidate.py支持固定hash本地wheel；verify_onnx_candidate.py分层有界执行；onnx_candidate/export_model.py与compare_model.py；test_onnx_candidate.py。M4局部改动另列于M4-QUERY-REWRITE-CONTRACT-v1.md，不混作ONNX容量通过。人工裁决/部分已审集/下一6题文档已记录。原reports、wheel、模型和profile不入版本库。

2026-11-05图融合实验新增scripts/onnx_candidate/optimize_model.py、scripts/compare_onnx_fusion.py、tests/test_onnx_fusion.py，verify_onnx_candidate.py追加只读源optimize阶段。仅本地实验工具，无生产代码变化。72相关T0通过；原版A1完成、候选数值失败后停止加压，不采用。详情M5-ONNX-FUSION-v1.md。

2026-11-05质量基线新增scripts/evaluate_m5_reviewed_bm25.py及tests/test_m5_reviewed_baseline.py，111相关T0通过；人工投影仪6题裁决、18条partial集与打印机下一组待审材料已同步。无生产代码变更、无模型或业务写入。M5-QUALITY-BASELINE-v1.md记录18题词法结果和限制。

2026-11-05用户委托代复核：新增docs/M5-ASSISTANT-REVIEW-v1.json/.md、M5-ASSISTANT-REVISION-PROPOSALS-v1.json、M5-ASSISTANT-REVISION-QUEUE-v1.md；audit_m5_assistant_review.py及14项边界测试。42相关T0通过；原595条与语料/24人工裁决不改，523一致/48待修订单列助手结果。

2026-11-05助手全权裁决：evaluate_m5.py追加v2响应行为/实际动作边界计分，build_m5_adjudicated_v2.py及acceptance/m5/adjudicated-v2/{dataset,corpus,manifest}.json生成616题，48项问题落地。tests/test_m5_adjudicated_v2.py新增专项、142相关T0通过；无生产代码变更，详见M5-ADJUDICATED-v2.md。

2026-11-05生产组件离线诊断/P0修复：新增scripts/evaluate_m5_offline_chain.py、tests/test_m5_offline_chain.py、app/agent/claim_scope.py、docs/M5-OFFLINE-CHAIN-v1.md；修改critical_facts.py/publication_guard.py与对应固定事实/workflow流式回归。仅临时SQL与合成回执，生成/Judge/业务动作调用0；保留80条政策语义反例缺口与全部旧未提交改动。

2026-11-05真实生成/Judge执行器准备完成，详见M5-LIVE-READINESS-v1.md及M5-LIVE-PLAN-v1.json。新增evaluate_m5_live.py/m5_live_budget.py及预算回归，79相关T0通过；616题/1027次SDK替身演练完成，真实调用0。无新明确额度时真实入口已验证拒绝，原660/660账本不变；计划仍PROPOSED_NOT_AUTHORIZED，M4/M5质量与性能门禁不升级。

2026-11-05用户授权真实诊断已完成：1027/1027请求、616题均有结果，原M4账本不变。固定反例6/100放行（确认5/争议1，冻结标签不改），超过2%门槛；105正确候选误拒0、20固定P0误放0。411生成输出由助手复核，非独立金标。另修复支付话题误当付款事实，209相关T0+10审计测试通过，616冻结Judge离线回放仅noanswer-046从误拒变接受，新调用0。真实源码与后修复来源分开记录，详见M5-LIVE-EVIDENCE-v1.md。新旧额度均耗尽，M4/M5整体及性能未通过；不发布/部署。

Judge v2候选：新增app/agent/judge_contracts.py、scripts/verify_m5_judge_v2.py、tests/test_m5_judge_contract.py/test_m5_judge_v2_integration.py/test_m5_judge_scope_cases.py、docs/M5-JUDGE-CONTRACT-v2.md；修改validation_llm.py、response_node.py、config.py及.env.example的候选版本开关。默认v1，未改业务.env/数据/费用账本；历史输出只读回放。

2026-11-06用户确认256次/输入预留400万/输出262144的新Judge v2方案，256/256已完成。新反例100零放行、新正确100零误拒，但20边界回复仅14允许、6契约/绑定未知；运行期间graph.py/intent.py并行变化，整体来源验收FAIL，不启用业务v2。新增错误码和下一派发前源码检查后121相关T0通过，新增调用0；旧报告/账本不改。详情M5-JUDGE-V2-LIVE-EVIDENCE-v1.md，全部新旧费用额度耗尽。
