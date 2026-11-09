# 本地重排batch20部署（2026-11-08）

## 已完成

API与Worker同步部署ai-customer:rerank-batch20-20261108，镜像sha256:d8b784957d0ce3df7d317a1b07bab53f7adcd8df10127180021a491724bed24a。九服务healthy；health/docs/openapi均200；pip check通过；数据库仍0013_agent_reliability。模型、向量空间、发布知识和退款规则未变，本轮聊天供应商调用0。

这是已部署基线ac4b75b0…上的最小补丁发布，不是当前脏工作区整体构建。只覆盖config.py、reranker_service.py、versioned_search.py三份源码：新增1–24批大小配置、替换原固定4批次、将批大小和新算法版本纳入检索缓存身份。通用默认4，本机显式20；超过20的受支持候选继续分批，单次完整分数/原文/截止/租约/回退规则保留。

运行容器127份app Python源码与隔离构建上下文逐份hash一致，API/Worker使用同一镜像、同一Redis namespace aicshub:local-models:whole-v1，redis单槽协调。无临时源码挂载。原运行环境与命令保留，有效环境变量仅新增RERANKER_LOCAL_BATCH_SIZE=20；TOP_K=3、Embedding批1、LLM温度/时限/并发没有采用上一轮未经验证的候选。

## 验证证据

- 最小镜像断网容器相关123项通过。生产镜像没有测试文件和aiosqlite，使用只读测试挂载及一次性容器安装本地wheel完成测试，未安装进业务镜像。当前聊天测试新增的两项温度/输出配置参数未属于本次部署，隔离测试副本仅排除这两个新增参数，原业务断言不改。初始缺git、测试文件、aiosqlite的准备失败记录保留，不计为测试通过。
- 部署前同一候选进程，三条合成查询对当前真实SQL发布知识/Milvus做batch4→20→4，9次检索成功、local重排、无降级，片段ID/正文hash/epoch一致。20候选重排HTTP5→1。不是全库语义或负载验收。
- 部署后API与Worker各3次同查询检索，共6次，均local、无降级、epoch25、每次重排1 HTTP，片段及正文与部署前一致。只运行检索，未调用理解/生成/Judge或退款。
- 切换前active Run=0，保留旧镜像及私有运行配置。首次Compose覆盖的null字段仍继承.env，校验发现4个额外但被旧源码忽略的字段；随后用!override精确替换环境再次创建API/Worker，最终确认仅重排批大小变化。未将中间配置视作最终收口。

完整证据reports/rerank-batch20-rollout-20261108/closeout.json、candidate.patch、container-tests-final.log、candidate-probe.json、runtime-verification.json、app-retrieval.json和worker-retrieval.json。

## 维护与回滚

本轮部署使用私有精确覆盖文件，其包含原运行密钥，只存Git忽略目录，不可提交或公开。普通Compose文件仍跟随工作区候选；维护本版本时应使用下面的精确覆盖，避免误把其他工作区候选部署或还原旧镜像。没有移动ai-customer:latest标签。

```powershell
docker compose -f docker-compose.yml -f reports/rerank-batch20-rollout-20261108/private/candidate-exact.yml up -d --no-deps --no-build app worker
```

已保留旧镜像ai-customer:before-rerank-batch20-20261108及原配置。回滚命令如下（本轮未执行真实回滚切换）：

```powershell
docker compose -f docker-compose.yml -f reports/rerank-batch20-rollout-20261108/private/rollback-exact.yml up -d --no-deps --no-build app worker
```

如出现重排截止/失租显著增加、不完整分数、真实检索降级或API/Worker版本不一致，先停止扩大变更并回滚。回滚仅切API/Worker镜像与配置，不删卷、不重建索引、不迁移SQL。切回后需核对健康及检索。旧JSON覆盖文件仅为历史准备产物，不可再用于部署。

本轮不声称完整真实聊天验收、生产并发容量或长期稳定性。工作区其他性能候选、温度/超时配置入口仍未随本镜像部署。
