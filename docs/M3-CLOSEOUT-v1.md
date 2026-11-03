# M3知识生命周期固定范围 v1

日期2026-11-03，基线a063d13。用户授权M3实施并验证；原未跟踪方案/规范/开发脚本保留，不操作业务库、知识库、卷或部署，不推送。

必做OPT-11全部AC-11-01～12；OPT-12正确性AC-12-01/02/03/04/06/07/09/11/13，共21项。AC-12-05/08/10/12的模型质量/证据覆盖校准留原计划M4/M5，不以M3替身宣称真实模型质量通过。必要的版本/权限过滤与完整分块来源属于本阶段，不能前缀截断破坏必要条件。

切片：1.SQLAlchemy稳定文档/不可变版本/章节位置与hash、legacy ID映射、CAS发布/软删除与知识epoch。2.严格批量Embedding/指纹隔离、新collection适配、持久index Job及故障恢复/上传限制。3.独立双路召回、明确score_type/降级、独立候选/重排/证据数量、epoch缓存隔离及真实依赖/浏览器验证。

传播窗口冻结为下一次检索的SQL快照：每请求检查SQL epoch并获取已发布活动版本，缓存键含该epoch；生成前复核版本，期间变化则拒绝使用旧证据。不依赖Redis通知才失效。并发编辑基于expected_version/CAS，索引失败保留旧发布指针，重复Job只处理同一version。

测试T0临时SQLite禁网络；T1本次专属MySQL/Redis/Milvus资源及合成向量；C供应商HTTP协议替身（含乱序/缺项/错维度），不当真实模型效果。真实模型如需费用单独授权。每切片通过后独立本地提交；最终逐项列证据、失败、NOT_RUN；未经验证条目不标PASS。

M3相关既有基线：35 passed，reports/offline/20261103-171446-448324。核实缺陷：不稳定doc_db/doc_mysql/随机ID、MySQL按标题删除/旧版停用遗漏、索引异常吞掉、空向量补零/错维度截断、Embedding失败短路BM25、RRF量纲混用、缓存/worker旧知识未版本隔离。

状态：M3冻结21项已完成验收。真实本地Ollama Embedding与Infinity Reranker完成索引/检索/重启补验；供应商免费额度探测HTTP400另记失败；本地Ollama/Infinity作为实际部署provider已补齐Embedding、索引、搜索及重启验收。证据见M3-EVIDENCE-v1.md与M3-GATES-v1.json。
