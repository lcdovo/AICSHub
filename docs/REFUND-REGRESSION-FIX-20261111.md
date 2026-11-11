# 退款回归与离线测试修复（2026-11-11）

单项逐项理解的退款申请被通用单项旁路排除，未创建任务，导致补问原因、前置查单、预览和后续状态恢复失败。任务入口现在保留after_sales任务；普通单项知识问答旁路仍保留。权限、独立Judge、本轮确认和模拟申请限制不变。

离线业务API适配器补齐生产生命周期中的MCP预载和关闭，避免冷启动占用首次工具截止时间。评测准备/校验与对应测试使用显式closing关闭SQLite连接，保留事务提交/回滚，避免Windows临时数据库文件占用。新增异常拒绝路径连接关闭回归。Windows路径长度测试使用固定PureWindowsPath样例，保持原长度断言。新增scripts/Dockerfile.offline提供Git和aiosqlite测试依赖，生产镜像不新增Git。

## 验证与限制

- 原退款失败最小两例先复现；最终相关五文件70项通过，包括原18项退款失败。
- 最终启用配置容器262项通过，Windows评测/安全/追溯相关48项通过；源码稳定，网络阻断尝试0。
- 全仓四分片：已完成914项、489项通过；另一片735通过/1个MCP启动失败，该项独立冷启动通过、整个可靠性文件串行34项通过。对应分片复跑及最后分片在Docker运行中断时退出255，未完整结束，不能宣称全仓全绿。
- 首次Windows挂载全仓运行因读取过慢停止；源码快照首片因遗漏static目录收集失败，补齐后914项通过。所有原失败/中断记录保留，不改写旧成绩。
- Python编译、候选pip check通过，候选225份Python实现文件与工作区SHA一致。专用测试镜像构建成功并验证Git/aiosqlite可用。
- 无付费模型调用；上述为隔离模型替身及真实本机部署检查，不构成新真实模型语义验收。

## 本机发布

API与Worker目标镜像ai-customer:refund-entry-fix-20261111，SHA256为f8ba3733fd4058b84ec6dc4ffb3e7c6e9932f9b882e703d736b848e56bc5ed6c。沿用前次完整发布镜像的静态资源、依赖与精确环境，数据库仍0014，无新迁移。上一完整发布镜像保留。

私有证据、精确部署与回滚覆盖位于.local/refund-regression-fix-20261111/，不得提交。维护使用：

```powershell
docker compose -f docker-compose.yml -f .local/refund-regression-fix-20261111/candidate-exact.yml up -d --no-deps --no-build app worker
```

回滚将candidate-exact.yml替换为同目录rollback-exact.yml；保留0014与所有数据卷。全仓测试期间Docker运行约19:55中断，主依赖停止；本次先恢复原依赖服务，再部署API与Worker，不删除卷或旧评测账本。

部署后API与Worker同镜像、225份Python源码哈希一致、精确环境及命令不变；九个业务服务healthy，health/docs/openapi/首页HTTP 200。两容器均以合成理解数据核对单项退款进入任务且首步查单，未派发模型或业务工具、无新增退款。
