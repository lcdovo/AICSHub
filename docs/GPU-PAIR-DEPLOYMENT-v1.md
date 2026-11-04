# 双GPU候选部署与切换边界 v1

2026-11-04。用户授权按Ollama GPU + Infinity GPU方案部署Qwen3-Embedding-0.6B与BAAI/bge-reranker-v2-m3。核验时两个owner=gpu-model-switch候选容器已经存在并运行，本轮复用并验证，没有冒称本轮创建，没有重建既有容器或修改业务.env/Compose路由。

| 用途 | 运行容器 | 宿主端口 | 已核验配置 |
| --- | --- | --- | --- |
| Embedding候选 | aicshub-gpu-embedding-candidate | 127.0.0.1:8013/v1 | Ollama/Qwen3 0.6B，Q8_0，digest ac6da0dfba84，1024维，ollama ps显示100% GPU，parallel1 |
| Reranker候选 | aicshub-gpu-v2m3-candidate | 127.0.0.1:7999 | Infinity/BGE-v2-m3，显式CUDA/float16，batch4 |
| 既有业务 | ai-customer-embedding-local-1 / ai-customer-reranker-1 | 8012 /7997 | CPU Embedding与旧BGE-large保持运行 |

compose.gpu-candidates.yaml记录固定镜像摘要、GPU请求、loopback端口及外部已有模型缓存卷，docker compose config --quiet通过。该文件为独立候选栈，不要和业务docker-compose.yml叠加。现有候选是手工管理、占用相同container_name，本轮没有对其执行compose up；要让Compose接管必须另做明确的停机/重建安排，不能直接运行覆盖。模型权重HF revision仍需进一步冻结；镜像固定不等于模型权重永远固定。

评测入口支持独立embedding-base/model与reranker-base/model，服务目录确认目标型号；Embedding地址仅HTTP127.0.0.1带显式端口且/v1，无凭据/query/fragment，不继承宿主业务配置。APIEmbedding使用无重试和1024维严格检查。应用生产Python、数据库schema、业务索引未改；原BGE-large专用分批未自动应用到v2-m3。

## 分层验证

- T0：新增7项Embedding端点守卫，与原端点/指标/路线/预算回归共60 passed（reports/offline/20261104-151244-888266）；compileall app scripts、Ruff F401/F821、git diff --check通过。不是全量回归。
- 双GPU真实检索：reports/m5/local-20261104-150838/report.json，临时SQLite、新Milvus collection、12合成开发查询cold/hot各一次。Embedding GPU与v2-m3 CUDA真实调用，重排12/12 local、无回退，向量维度1024；原5秒预算。重排P95=2713.665ms、端到端P95=3839.074ms，原2秒性能目标未通过。源码指纹7887e7c0642508d7c6a684b06f76464c37306292bbd05b7801be412f9c21db48，稳定完整。
- 同时推理：reports/gpu-pair/20261104/compatibility-concurrency.json，4轮，每轮Embedding4文本与重排12候选同时发起，全部成功；Embedding约739～816ms、重排约840～1007ms。轮后GPU快照6679/8188MiB，空闲1278MiB；仅轮后采样不是显存峰值，没有200次/并发10/长文本承诺。
- CPU/GPU Embedding对照：同digest、4合成短文本，余弦约0.999667～0.999727，最大维度差约0.00309～0.00446；不能把相同模型名/维度解释为逐值等同，尚不足以宣称对业务旧索引完全兼容。生产索引未改未清理。
- runtime.json记录候选容器ID/镜像/端口/GPU与卷，不保存宿主.env或密钥。候选容器本来就在运行，保持其运行状态；未清空/删除/迁移数据或停止其他项目服务。外部模型/聊天调用0，预算660/660保持。

## 当前结论与下一步

候选服务已运行并完成有限合成验证，完整业务部署切换尚未执行；M5整体仍INCOMPLETE。原M4门槛和人工595候选复核未通过。下一步冻结权重/设备/精度和显存配置，完成Embedding索引兼容/必要时独立新空间重建、200有效运行与并发长候选验证，再执行可回滚业务切换。用户这次部署授权覆盖本方案实施，不需为已授权技术步骤重复确认；不能因此忽略原验收门禁或删除旧数据。

## 后续切换前压力与索引验证

详见GPU-PAIR-ACCEPTANCE-v1.md。本轮200不同合成请求（12重复语义簇，每次20条369～425字候选）：串行96/100成功、并发10为57/100，全部47超时保留；重排P95约3.38/5.02秒，暂不切业务。12文档/12查询隔离索引的混合top1/top3未变，但完整排序2/12变化，不批准混用旧空间。候选/业务容器、数据卷及旧索引均未改。

共享预算代码已补（见LOCAL-INFERENCE-BUDGET-v1.md），限同一应用进程的本地HTTP出口；不同API/Job worker不共享预算，不能把它当GPU全局限流。200次真实同负载串行100成功，并发10仅5成功/94明确繁忙/1超时，依旧不切业务；后台一次真实同进程竞争仅证明有限延后行为。

整轮准入与Redis协调功能为候选（WHOLE-REQUEST-ADMISSION-v1.md），默认process，配置模板透传但未对业务执行部署。T0/Redis有限场景通过，真实200压力依然FAIL；启用Redis后也必须所有API/Job worker共享同Redis DB/namespace和新版入口，其他模型客户端不受此代码约束，GPU已发任务无法靠租约强制停止。当前不切业务GPU。
