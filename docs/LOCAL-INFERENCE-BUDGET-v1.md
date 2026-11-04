# 本地模型共享预算与背压 v1

2026-11-04。OPT-12/14/16，关联AC-12-02/03/05/13、AC-14-05/07/10、AC-16-04/08局部。目标为停止在应用进程里对本地Embedding/重排无界派发，繁忙和超时保留真实结果，不据此宣称M5性能通过。业务GPU路由与数据库schema/数据保持。

## 预算边界

本地模型HTTP出口共用同一进程内LocalInferenceScheduler：最多1个在途客户端调用、2个等待者，前台等待最多500ms、后台50ms，等待计入既有5秒阶段截止。检索线程池复制context，父截止时间与观测传到模型服务，晚到返回拒绝。前台优先；已经进入的HTTP调用完成或异常返回前不释放槽。外部供应商不加入该预算，不拆付费请求。调度只识别loopback和明确本地服务名。

这不是跨进程/GPU驱动互斥。不同API worker、独立Job worker或其他调用者拥有各自进程预算，不能把active1宣称为整个Docker/主机只推理1次。HTTP超时返回后服务端可能仍在推理；释放客户端槽不证明CUDA任务已停止。当前部署未被改成单worker，也没有引入分布式锁。多worker共享预算仍待独立切片/真实集成。

后台索引明确使用background优先级。遇到LocalModelBusy/Deadline，保留旧SQL发布指针，Job=retrying、释放租约、next_run指数退避（上限60秒）；尝试上限后failed/attempts_exhausted。不能因本地繁忙将新版本发布，不能无界重试；前台请求持续存在时后台可能达到尝试上限，需要从Job记录核查后人工重试。

## 公开行为与观测

每次本地HTTP记录固定purpose/priority/status、dispatched、queue_ms、inference_ms，不记录用户文本、身份、URL或凭据。status只证明HTTP出口执行/完成情况，不是回答事实通过、SQL业务成功或实际GPU完成。模型契约校验和独立回答校验继续有效。

检索metadata包含local_inference、本次local_model_admission（busy/deadline/cache/completed_or_not_local）及local_model_scheduler_scope=process。Embedding繁忙可保留BM25，重排繁忙保留融合排名，明确degraded原因；不能凭高分补造证据。证据不足时聊天明确提示繁忙/超时和稍后重试，不隐式转人工/执行动作。正常查询和已有业务回执不因调度而授权新动作。

缓存身份纳入调度算法版本。瞬时繁忙/超时缓存行为单独检查，避免一次过载造成长期劣化；本轮后续验证记录以源码指纹解释。文本缓存命中可能不发HTTP，不能把缓存时间算作模型推理时间。

## 验证计划

- 确定性并发：active1、等待2、溢出零派发、超时/异常清槽、前台优先、迟到不能放行、context恢复/跨线程传播。
- 服务接入：Embedding与Reranker共用调度、外部服务不受本地预算影响、繁忙不自动重试、HTTP超时为deadline。
- 隔离SQL Job：繁忙延后、最终尝试收敛失败、旧发布版本保持，不能发布半成品。
- 聊天：无证据时繁忙/超时文案真实，没有转人工或额外业务工具。
- 真实GPU：用同一200合成负载（20候选/369～425字、并发1/10），启用--scheduled走生产模型服务；计数PASS、BUSY和FAIL分别报告，BUSY不算完成。直接provider与服务封装路径不同、同机负载变化，不作因果性能提升承诺。

原200请求基线仍保留：串行96/100、并发10为57/100，47次ReadTimeout，P95超过门槛；当前代码验证及新压力报告待结束单列。无真实付费聊天/Judge调用，595质量候选仍待人工复核。

## 真实单进程调度压力（初步封存）

执行python scripts/verify_gpu_pair.py --count 200 --skip-compatibility --scheduled，报告reports/gpu-pair/acceptance-20261104-155454/report.json。与原基线同一合成工作负载，完整20候选/369～425字、不同请求ID但12重复语义簇；走APIEmbedding/RerankerService实际出口并共享同进程scheduler。不是HTTP直接provider旧路径，也不代表完整REST/WS/SQL Run链。

| 并发 | 请求 | 完成 | 明确BUSY | 其他失败 | 实际HTTP派发 | 重排阶段P95（含拒绝/失败） | 总耗时P95（全部请求） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 100 | 100 | 0 | 0 | 200 | 1426.097ms | 2119.732ms |
| 10 | 100 | 5 | 94 | 1 deadline | 19 | 6028.028ms | 3184.971ms |

BUSY94次全部计未完成，不从100分母剔除。一个deadline可能HTTP实际返回已超过5秒，迟到结果拒绝而非成功；HTTP timeout不是硬墙钟，最大总耗时8489.320ms。字段queue_p95_ms约511.77ms是实际调度采样，500ms为准入决策阈值，不等于任何操作系统负载下回调能在500ms内完成。并发10组的重排P95仅实际走到重排阶段的请求（含其中的拒绝/失败）；没有进入重排阶段的请求保留整体失败与总耗时，不补0。

串行观察比旧基线好，但不能声称由调度保证：设备共享、桌面负载和时间不同，而且两轮调用封装路径也不同。高并发下低超时主要伴随大幅拒绝，完成率5%，整体FAIL；它保护有限出口预算但远未实现并发10服务能力。不可依据串行阶段P95<2秒或全部请求更快拒绝宣布M5达标。

源码起止fd56ba046f5bb821d107482b8b947f26d322b9eea3acd2ba992957d39f54d5f5，稳定且快照完整。原200次47超时报告保留，不用新拒绝覆盖它。后台真实高并发压力和独立worker间共享未测，Job延后此前只有隔离SQLite/替身验证，收尾再补一次真实GPU单进程探测（见后文）；595候选与真实聊天/Judge质量未用此数据替代。

## 最终回归与缓存收口

全量T0 1024 passed/0失败（reports/offline/20261104-155454-041883，519.83秒），source_tree_stable/source_snapshot_complete=true、网络禁止且blocked_network_operations=0，源码与上述真实压力同为fd56ba046f5bb821d107482b8b947f26d322b9eea3acd2ba992957d39f54d5f5。它在最终缓存收口前，不冒称是后续代码全量结果。

缓存检查新增反例先红：两项准入结论丢失（reports/offline/20261104-160507-175646），调整断言顺序后实际复现恢复请求仍命中busy/deadline缓存（160538-448434），均2失败/保留报告。修复为准入结论同时读取本轮events和明确错误原因，local_model_busy/local_model_deadline结果不写检索缓存；正常恢复后重新执行，随后健康缓存可命中且local_inference为空。外层线程TimeoutError同样归为deadline，不能缓存为普通依赖失败。不得丢弃这些失败以使测试变绿。

最终共16项新增调度/背景/聊天/缓存用例，连同受影响API/轮次/检索/Job/模型与报告器161 passed（reports/offline/20261104-160723-420202）。源码8b0bca0d9d1e0298d70d9ecde83315b72818699ff2e6d6ab38d66f1c444d324b，稳定完整、网络禁止且blocked_network_operations=0。compileall app scripts、Ruff F401/F821、git diff --check通过。没有因为收尾小范围cache逻辑变化再重复全项目测试；全量前版和收尾受影响证据按各自源码解释，不能合并成新1027全量声明。

真实后台单次探测reports/gpu-pair/background-budget-20261104.json：新临时SQLite实际Job、真实GPU Embedding/Reranker同进程；前台20候选重排运行时后台索引受预算约束，retrying/local_model_busy、租约释放，published_version=1保留，前台20条返回、HTTP出口inference约800ms。仅一次手工合成探测，不当跨worker/MySQL/长时验收，未发布新后台版本或操作业务数据。

压力期间显存307采样最高7048MiB、最低free909MiB，不是独占显存/严格峰值；后台单次探测不等于200次压力中有持续后台竞争。原2秒P95、业务完成率、M4质量、595条人工标注、真实聊天/Judge、分布式共享队列及长期稳定性仍未通过。此次不切业务GPU、不迁移、不清库/清卷、无付费模型新增请求，保留原未提交文件。
