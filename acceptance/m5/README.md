# M5数据与报告

`development.json`包含37条助手编排的合成开发夹具：12条可回答、6条无答案/部分答案、11条审查反例、8条正确回复。12份合成知识在`corpus.json`。这些是调试/回归数据，不是300/60/100/100独立留出，不把ID替换或同一语义变体累计为独立数量。SHA256和来源见`dataset-manifest.json`。

独立留出尚未准备/裁决；应由未参与调参的审阅者按原规范采样并核实chunk/version/必要条件、可允许事实、禁止论断，近义簇不能跨开发/留出。标注字段label_source=human_reviewed及reviewer只记录声明来源，不提供身份认证，不能由程序代填为“已人工”。输出关键论断还需要独立标注，不能直接采用LLM Judge自评分。

新报告入口（只读JSON，不执行网络/模型）：

```powershell
python scripts/evaluate_m5.py --dataset acceptance/m5/development.json --observations reports/m5/run.json --output reports/m5/quality.json
# 真正holdout还需 --development-dataset acceptance/m5/development.json 核对簇隔离
```

Observation字段：case_id、ranked_chunks和evidence_chunks（每条含chunk_id/version_id）、covered_conditions、observed_support、accepted、latency_ms、error、claim_support（独立人工输出标注，未标注为null）。Run声明dataset_sha256/source_fingerprint、layer（T0/T1_LOCAL_MODELS/T2）、mode（bm25/dense/hybrid/rerank/rewrite）、cache（cold/hot）、configuration的Embedding/revision/Reranker/prompt/index版本、output_label_source/output_reviewer。

报告按完整数据集分母，缺样本/失败保留；Recall与Hit分开，nDCG用事先等级、版本精确匹配、所有必要证据/条件参与覆盖。没有独立标注时关键论断支持率null；空样本、缺延迟/配置/层次、NaN、伪造hash、未知/重复ID不得PASS。非单次合格报告退出2；即便PASS_SINGLE_RUN_ONLY也不代表全部消融/冷热、前置M4或整体M5通过。无效输入只记错误类型，不打印原输入/密钥。

旧evaluate_rag.py按标题首次命中的历史报表及evaluate_rag_quality.py的模型自评分均不作为M5 chunk级/独立人工质量验收。
