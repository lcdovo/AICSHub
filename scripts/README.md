# 脚本导航

所有命令从项目根目录执行。下列分类用于查找，不构成运行授权；带有evaluate、verify或preflight的文件也可能访问模型或外部服务，应先阅读实现与对应固定方案。

## 常用入口

- 日常启停：根目录 `start.bat` / `stop.bat` 调用 [runtime.ps1](runtime.ps1)，复用已有容器，不构建或迁移。

- 默认隔离回归：`python scripts/test_offline.py`
- 启用配置链路的隔离回归：`python scripts/test_offline.py --profile active`
- 工作区与部署镜像的同版本验证：`python scripts/verify_offline_release.py`
- 启动主栈：`./scripts/start.ps1 -SkipSeed`，会启动服务并执行迁移。
- 停止服务：`./scripts/stop.ps1`，数据清理选项需单独授权。

`_dev_run_import.py` 含清空再导入行为，仅保留为历史开发工具，禁止用于常规验证。`import_rag_documents.py` 也会写库和创建索引任务。付费评测、部署和数据写入须遵守[项目指南](../AGENTS.md)及相应方案；不得借整理文件重置账本或自动续验。

## 启动、部署与数据写入

- [_dev_run_import.py](_dev_run_import.py)
- [deploy_rerank_batch20.py](deploy_rerank_batch20.py)
- [import_rag_documents.py](import_rag_documents.py)
- [run_worker.py](run_worker.py)
- [seed_ecommerce_orders.py](seed_ecommerce_orders.py)
- [start.ps1](start.ps1)
- [stop.ps1](stop.ps1)

## 业务、模型与阶段验收

- [audit_m5_assistant_review.py](audit_m5_assistant_review.py)
- [audit_m5_live_results.py](audit_m5_live_results.py)
- [build_m5_adjudicated_v2.py](build_m5_adjudicated_v2.py)
- [build_m5_judge_v2_cases.py](build_m5_judge_v2_cases.py)
- [business_acceptance.py](business_acceptance.py)
- [business_acceptance_execute.py](business_acceptance_execute.py)
- [business_api_adapter.py](business_api_adapter.py)
- [business_evidence_preflight.py](business_evidence_preflight.py)
- [business_http_boundary.py](business_http_boundary.py)
- [business_refund_execute.py](business_refund_execute.py)
- [continue_business_acceptance.py](continue_business_acceptance.py)
- [evaluate_agent_task_acceptance.py](evaluate_agent_task_acceptance.py)
- [evaluate_item_judge_v2.py](evaluate_item_judge_v2.py)
- [evaluate_m5.py](evaluate_m5.py)
- [evaluate_m5_judge_v2_live.py](evaluate_m5_judge_v2_live.py)
- [evaluate_m5_live.py](evaluate_m5_live.py)
- [evaluate_m5_local.py](evaluate_m5_local.py)
- [evaluate_m5_offline_chain.py](evaluate_m5_offline_chain.py)
- [evaluate_m5_reviewed_bm25.py](evaluate_m5_reviewed_bm25.py)
- [evaluate_model_first_admission.py](evaluate_model_first_admission.py)
- [evaluate_model_first_quality.py](evaluate_model_first_quality.py)
- [evaluate_multi_intent.py](evaluate_multi_intent.py)
- [evaluate_multi_intent_oracle_v2.py](evaluate_multi_intent_oracle_v2.py)
- [m5_live_budget.py](m5_live_budget.py)
- [multi_intent_oracle.py](multi_intent_oracle.py)
- [prepare_agent_task_acceptance.py](prepare_agent_task_acceptance.py)
- [prepare_item_v2_acceptance.py](prepare_item_v2_acceptance.py)
- [refund_language_acceptance.py](refund_language_acceptance.py)
- [refund_language_execute.py](refund_language_execute.py)
- [revalidate_business_refund.py](revalidate_business_refund.py)
- [revalidate_model_first.py](revalidate_model_first.py)
- [review_business_refund.py](review_business_refund.py)
- [run_item_v2_acceptance.py](run_item_v2_acceptance.py)
- [run_item_v2_atomic_acceptance.py](run_item_v2_atomic_acceptance.py)
- [run_multi_intent_live.py](run_multi_intent_live.py)
- [verify_m5_judge_v2.py](verify_m5_judge_v2.py)

## 性能、配置与实验

- [benchmark_intents_guarded.py](benchmark_intents_guarded.py)
- [build_onnx_candidate.py](build_onnx_candidate.py)
- [check_parameter_candidates.py](check_parameter_candidates.py)
- [compare_attention_pressure.py](compare_attention_pressure.py)
- [compare_batch_pressure.py](compare_batch_pressure.py)
- [compare_gpu_batch.py](compare_gpu_batch.py)
- [compare_onnx_fusion.py](compare_onnx_fusion.py)
- [compare_rerank_requests.py](compare_rerank_requests.py)
- [diagnose_gpu_capacity.py](diagnose_gpu_capacity.py)
- [evaluate_gpu_arrivals.py](evaluate_gpu_arrivals.py)
- [evaluate_intents.py](evaluate_intents.py)
- [latency_results.py](latency_results.py)
- [measure_intent_e2e.py](measure_intent_e2e.py)
- [offline_profiles.py](offline_profiles.py)
- [optimization_gate.py](optimization_gate.py)
- [preflight_onnx_runtime.py](preflight_onnx_runtime.py)
- [profile_gpu_concurrency.py](profile_gpu_concurrency.py)
- [profile_local_pool.py](profile_local_pool.py)
- [profile_model_calls.py](profile_model_calls.py)
- [runtime_onnx_probe.py](runtime_onnx_probe.py)
- [verify_gpu_pair.py](verify_gpu_pair.py)
- [verify_onnx_candidate.py](verify_onnx_candidate.py)

## 离线验证与其他维护工具

- [dev.ps1](dev.ps1)
- [diagnose_local_admission.py](diagnose_local_admission.py)
- [eval_gate.py](eval_gate.py)
- [evaluate_answers.py](evaluate_answers.py)
- [evaluate_e2e.py](evaluate_e2e.py)
- [evaluate_m4_live.py](evaluate_m4_live.py)
- [evaluate_rag.py](evaluate_rag.py)
- [evaluate_rag_quality.py](evaluate_rag_quality.py)
- [evaluate_rag_structure.py](evaluate_rag_structure.py)
- [reindex_local_models.py](reindex_local_models.py)
- [setup_m3_test.py](setup_m3_test.py)
- [test_local_admission_redis.py](test_local_admission_redis.py)
- [test_m1_real.py](test_m1_real.py)
- [test_m2_real.py](test_m2_real.py)
- [test_m3_local_models.py](test_m3_local_models.py)
- [test_m3_model.py](test_m3_model.py)
- [test_m3_real.py](test_m3_real.py)
- [test_m4_real.py](test_m4_real.py)
- [test_offline.py](test_offline.py)
- [test_provenance.py](test_provenance.py)
- [verify_offline_release.py](verify_offline_release.py)

## 实验子目录

- [onnx_candidate/](onnx_candidate/)
- [runtime_attention_probe/](runtime_attention_probe/)
