# 记忆、会话删除与安全修复本机发布（2026-11-11）

用户明确要求将工作区已实现而未部署的功能全部部署，并提交推送Git。本次API与Worker统一为`ai-customer:memory-full-20261111`，最终镜像`sha256:732fa406e83b0f5da704a973734959bf0351ac6f337658218227fd0486bdb240`。数据库升级至`0014_user_memory_entries`；检查时九个客服服务均healthy。

## 发布范围

- 有来源的结构化会话摘要、无关长期历史允许空召回、隐式追问衔接及显式换话题保护。
- 理解/决策/回答的共享背景预算、统一摘要契约与持久summary Job。
- 有来源、180天有效期、可更正/撤回的语言和详略偏好；本轮要求优先，偏好不授予业务动作权限。
- 聊天列表删除入口、确认框、进行中禁用、删除后的草稿/缓存清理与导航。
- 工作区已有的严格Judge引用来源/标点检查、公开异常/Run事件错误字段清理和UTC时间统一。
- 保留已部署的回答反馈及管理界面、Reranker batch20、RAG阈值0.4和当前模型/准入配置。新增显式环境项仅`USER_MEMORY_MIN_SIMILARITY=0.65`；未更换知识、模型空间或重建索引。

发布前发现Worker严格schema检查仍停留在0013，已更新至0014并同时检查能力执行账本和偏好表存在。回归验证旧revision、仅修改revision但缺表时仍拒绝启动，完整schema才通过。

## 备份、构建与上线检查

生产MySQL完整逻辑备份保存于Git忽略的`.local/full-release-20261111/database-before.sql`，大小及SHA-256见同目录`backup-manifest.json`。备份、精确Compose覆盖和环境快照含私有数据/凭据，不得提交或输出。

依赖文件与原运行镜像一致，沿用原依赖层，将公开工作区源码快照及已构建静态资源打包。首次以裸image ID作为Dockerfile基底被当作远端仓库而构建失败，随后使用本地固定tag解决；失败未切服务。首个后端候选`99312f13…`已上线后，HTTP字节核对发现`.dockerignore`排除了新静态资源；修正该发布上下文的打包规则后重建为最终`732fa406…`，后端实现文件保持一致。未把首次旧前端写成最终发布通过。

切换前检查accepted/running Run为0，停止API/Worker后迁移，再同步重建。未删除数据卷、批量回填摘要或偏好、修改旧验收账本。最终API/Worker的290个实现文件与发布来源逐字节核对一致，无临时源码挂载。

- 最终实际HTTP首页与构建产物一致，247份静态资源逐文件字节一致，包含本次删除会话入口。
- 真实MySQL合成数据验证跨会话并发首次写入、按消息顺序更正、旧Run迟到不覆盖、撤回后不被旧消息复活。
- 常驻Worker实际消费合成summary Job，生成带条件及来源的`structured-excerpts-v2`摘要。
- 实际HTTP删除合成会话、再次删除404；删除来源会话后偏好停止使用。
- 实际HTTP回答反馈点赞、改为点踩、读取、管理员原文查看及401/403/404边界通过。
- `/health`、`/docs`、`/openapi.json`和首页通过。合成账户、会话、消息、偏好、反馈与summary Job已清理。

本次付费模型调用0。上述真实依赖检查不等于真实聊天模型语义、生产容量或一般长期记忆可靠性验收；0.65阈值仍未用固定语义留出集校准。

## 自动化验证

- 前端`npm test`：36通过；`npm run build`通过。
- 最终后端宿主active记忆/Worker三文件：61通过。
- 隔离容器active默认矩阵：262通过，0失败，源码稳定、网络阻断尝试0。
- 隔离容器active记忆/Worker/反馈/Run/Judge六文件：119通过，0失败，源码稳定、网络阻断尝试0。
- 容器依赖`pip check`、Python编译通过。测试镜像只增加离线测试依赖aiosqlite及测试文件，未部署该测试镜像。
- 容器测试使用后端候选`99312f13…`；最终静态打包修复未改变这290个实现文件，已分别核对最终API/Worker。不得称最终镜像一次全链真实模型验收。
- 首轮宿主baseline/active在发现Worker迁移门槛需修复后中止，不能算通过；首次非root测试容器因报告目录权限在收集前失败，修正测试运行身份后执行，原记录保留。

全仓隔离容器baseline共2942项：2905通过、23失败、8错误、6跳过；源码稳定且收集完整、网络阻断尝试0，未通过全仓门槛。具体区分如下：

- 18个退款业务失败均以改动前HEAD源码重新复现：短原因补问1项、换订单/任务投影4项、退款语言契约/读取计划13项；原断言及失败记录保留，本次未改写无关退款逻辑。
- 1个Windows路径长度断言在Linux镜像失败；4个评测准备用例及8个安全/源码追溯用例因测试镜像不含Git而失败或setup错误。对应宿主定向回归分别27、2、8项通过，不能回写容器全仓为通过。
- 追加宿主评测文件首次12项断言通过，但清理临时SQLite时遇到Windows文件占用导致入口非零退出，该次不作为完整通过证据；随后只复验上述2个缺Git用例，入口正常完成。

原始证据集中在`.local/full-release-20261111/`；其中公开结论可见本文件，私有文件保持忽略。

## 维护与回滚

保持本次精确部署：

```powershell
docker compose -f docker-compose.yml -f .local/full-release-20261111/candidate-exact.yml up -d --no-deps --no-build app worker
```

0014为加表迁移，不自动删表回退。原镜像的Worker严格要求0013，不能直接用于已迁移数据库。已另制备仅修正Worker schema检查的旧功能兼容镜像`ai-customer:before-memory-full-schema14-20261111`（`sha256:c15a819143026fbce10dd6074e6e106f15d11572edca6aecf374efd28dd2e716`），保留原业务功能、配置和新表数据；其启动schema检查已在实际0014数据库验证。没有执行真实业务回切演练。

需要回退本次功能时使用兼容覆盖：

```powershell
docker compose -f docker-compose.yml -f .local/full-release-20261111/rollback-exact.yml up -d --no-deps --no-build app worker
```

原始运行镜像及精确配置另存为`ai-customer:before-memory-full-20261111`和`original-image-exact.yml`，仅用于追溯，不能绕过0014兼容检查直接回切。普通Compose的`latest`不保证指向本次发布，维护请使用以上精确覆盖。
