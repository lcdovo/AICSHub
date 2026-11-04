# 本地模型租约释放补偿与健康恢复 v1

后续[常驻服务容量对照](GPU-CAPACITY-DIAGNOSIS-v1.md)及[Embedding生命周期修复/常驻200门禁](EMBEDDING-HTTP-LIFECYCLE-v1.md)已完成局部验证；下方每case模型服务压力为历史结果，不能代表业务常驻连接池耗时。

2026-11-04。对应 OPT-12/14/16 局部；修复协调诊断中已复现的释放结果未知与单次清理中断。M4质量失败、M5质量/性能未完成、供应商额度660/660保持。

## 运行恢复和修复前观察

Docker 29.7.2正常响应，恢复前宿主可用内存约4.2GiB，GPU约1856MiB已用。核实owner标签与loopback端口后，启动已存在的两个gpu-model-switch候选及m3-closeout专用Redis。旧验证入口同时要求测试MySQL运行，另核实并启动同owner专用MySQL；未执行SQL迁移/导入。其余容器未操作，业务配置/路由/知识库/数据卷未修改。

健康报告：`reports/gpu-pair/recovery-20261104-185227/health.json`。Ollama的qwen3-embedding:0.6b/digest ac6da0dfba84、Infinity的BAAI/bge-reranker-v2-m3和测试Redis均响应。该报告只证明当时健康，不代替模型推理或长时稳定性。

第一次小负载入口因共用验证器发现测试MySQL未运行而提前退出，未派发模型；产生未完成来源目录，不计PASS。启动已核实测试MySQL后，小负载`reports/gpu-pair/acceptance-20261104-185301/report.json`保存两次冷启动预热deadline；之后串行5/5完成但重排P95=3238.470ms，总耗时P95=8003.705ms；并发10配置实际仅5请求，1完成/4BUSY。此报告FAIL，不能称并发10容量或性能通过；源码起止稳定完整，属于本片修复前版本。

## 行为变化

- Permit关闭且在途HTTP引用归零后，停止续租，Redis按原owner令牌执行条件RELEASE；仅第一次异常时再补偿一次，总计最多2次，不重试模型或重新准入。
- 明确记录`release_status`与`release_attempts`。`released`只表示收到删除1回执；`not_owner`表示条件删除返回0（可能此前已删/已过期/继任者持有），不声称已确认删除；两次异常则`unknown`，保留TTL回收。`pending`表示尚未到释放阶段。
- `_release_attempted`限制重复close不增加补偿次数；`_released`只代表确认删除。旧字段曾只代表尝试，诊断脚本同步新语义。
- 所有补偿复用同一token条件脚本，不执行无条件DEL，不删除新owner；在途HTTP结束前仍持有资格。失租后不允许后续派发，补偿成功也不恢复旧Permit资格。
- 清理自己的前/后台排队ticket分别尝试，前一个ZREM失败不跳过后一个。故障时ticket仍依赖原TTL；没有新增无限后台清理。
- 请求观测附`lease_release`状态/次数，诊断close保存相同状态，不记录owner、URL、密钥或异常正文。外层提前退出且HTTP尚在途时，scope观测可为pending；它不是最终释放完成通知。
- 调度版本升至whole-request-owner-lease-v2，自动参与现有检索缓存身份。默认process和业务端点保持；process释放返回是否匹配的明确结果。

限制：有界是命令次数，不是严格墙钟保证。生产Redis客户端单次socket/connect配置仍为200ms；操作系统停顿、库内部传输与原有finally清理可叠加。连续失联仍可能保守占位至TTL，不能据此承诺永久可用或服务端GPU任务取消。

## 验证

7项新增T0先红：`reports/offline/20261104-185423-981707`，7 failed，保留。覆盖短暂/持续释放异常、删除后丢回包、继任者保护、在途延后、观测脱敏及两个ticket独立清理。修复后最终相关回归135 passed（`reports/offline/20261104-185532-859945`，12.00秒），包含聊天轮次、重排预算、HTTP生命周期、关闭、准入及旧诊断。禁止网络、临时SQLite；不是最终源码全量测试。

真实专用Redis验证：`reports/gpu-pair/redis-admission-20261104-185515/report.json`，11场景通过。原7场景保留；新增4项在实际Redis键上注入客户端释放故障，分别验证短暂故障补偿、删除后丢回包、继任者不受影响、持续故障保留owner及正TTL/停止续租。故障不是停止真实Redis，持续故障场景核验后只条件清理本次测试键。

最终T0与Redis报告源码指纹均为`ea9bb8d7aff69139c31bac154658dd78fb8d6699ae161795cf753ef475c97c4b`，起止稳定完整。compileall app scripts、切片Ruff F401/F821、git diff --check通过。

## 真实性能复验

命令：`python scripts/verify_gpu_pair.py --count 200 --skip-compatibility --scheduled --whole-request --coordinator redis`。报告`reports/gpu-pair/acceptance-20261104-185603/report.json`，整体FAIL。使用原200不同负载字符串/12重复语义簇、20完整长候选、并发1/10各100、5秒模型阶段预算/10秒整轮准入，BUSY计未完成。另有2次预热，均PASS；预热不并入200分母，不抹去前一次冷启动超时。

| 并发 | 完成 | BUSY | 其他失败 | 重排阶段P95 | 全部请求总耗时P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 100/100 | 0 | 0 | 1118.007ms | 2866.133ms |
| 10 | 1/100 | 99 | 0 | 953.040ms | 29.704ms |

并发组仅1条到达重排，不能用该单点P95评价模型分布；总耗时P95低是快速拒绝造成，不表示服务能力改善。97条queue_full_or_expired、2条wait_expired；全部BUSY前模型派发0。获准的101条及两次预热均记录`lease_release.status=released`，无自然释放未知/协调故障；不能据此宣称未知故障已被普遍消除，故障边界由另11项Redis验证解释。

串行重排阶段达本报告2秒目标，但端到端P95仍约2.87秒；并发容量失败。当前active1/waiters2/500ms与突发负载下模型耗时不匹配是由拒绝分布支持的限制，不证明底层GPU只能承受一个请求，也不提供增大并发的安全结论。未调整这些参数、模型、候选数量或超时标准。

源码指纹仍为`ea9bb8d7aff69139c31bac154658dd78fb8d6699ae161795cf753ef475c97c4b`，起止稳定完整；工作负载hash为`188302e1a056e46cd3300aea9959a8c3d3356890b987bf67a6d5a00802fd5393`。显存463次采样最大6622MiB；压力后只读检查两个候选running、OOMKilled=false、RestartCount=0。采样不保证捕获瞬时峰值。

该脚本每case新建并关闭两个模型服务，仍不是业务常驻服务生命周期或完整REST/WS/RAG性能；HTTP池实际跨调用复用由前片loopback传输及T0证明，本报告不能单独证明业务跨请求连接复用收益。宿主/容器恢复与前片源码均不同，不把串行变化唯一归因释放补偿或HTTP池修复。本轮无新增全量、多API进程+真实GPU组合压力、真实业务知识索引兼容或质量验收。

候选与专用测试MySQL/Redis保留运行，未停止其他项目、业务服务或删除数据。下一步应在固定工作负载下单独比较常驻服务生命周期、准入等待/拒绝分布与GPU安全并发能力；先提出可验证容量方案，再评估参数或架构变化。当前不切业务GPU/Redis协调模式，不进入M6；人工595条候选及M4实体失败继续独立处理，无付费新增调用。
