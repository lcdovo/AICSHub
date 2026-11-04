# 协调 I/O、租约释放与模型等待诊断 v1

后续已完成 [重排HTTP连接复用与阶段预算修复](RERANKER-HTTP-LIFECYCLE-v1.md) 的有限验证；本报告的历史失败保留，租约释放及真实模型性能仍待验。

2026-11-04。本切片对应 OPT-12/14/16，以及 AC-12-05/11/13、AC-14-05/07/09/10、AC-16-04/08 的局部定位证据。未改变标准，未通过 M5 性能或完整验收。

## 已确认的退化链

真实报告：`reports/gpu-pair/io-diagnosis-20261104-171034/report.json`。随机 namespace、核实 owner=m3-closeout 的隔离 Redis、owner=gpu-model-switch 的两个 GPU 候选；仅合成输入，无付费服务。

1. 前50轮纯协调完成；redis-py 5.2.0 实际连接配置为0次自动重试、retry_on_error为空。排除了本次客户端隐式重试，不排除底层网络/宿主停顿。
2. 第二个Redis串行块首请求获得匿名租约 `011392707195`，Embedding HTTP返回后的owner GET在213.492ms超时。Permit将其视为失租，整轮期限当时未到。
3. 同一租约的RELEASE在231.651ms超时。内部 `_released=True` 只表示已尝试释放，不是Redis删除确认。close停止续租后无再次条件释放，未知结果依赖15秒TTL。这是故障时保守占位，不是已证明的永久泄漏。
4. 约5.8秒后快照仍见相同owner、PTTL=9227ms、前后台ticket均0。随后9条串行请求全部BUSY、无模型派发，证实“核验失败→释放未知→owner残留→串行拒绝”。下一块后owner不存在；报告结束Redis失联，最终状态不可核实。
5. 并发10组均为协调不可用，每条633～649ms：申请、补偿释放、finally清ticket的多个约200ms I/O顺序叠加，不是自动重试，也不全是500ms队列等待。finally首个zrem异常时第二个不会执行，ticket仍有原短TTL。

## 模型链路与测量归属

| 顺序 | 模式 | 并发 | 成功 | BUSY | 失败 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | process | 1 | 9/10 | 0 | 1 |
| 2 | redis | 1 | 10/10 | 0 | 0 |
| 3 | redis | 1 | 0/10 | 9 | 1 |
| 4 | process | 1 | 6/10 | 0 | 4 |
| 5 | redis | 10 | 0/10 | 10 | 0 |

原20条长候选、5秒阶段/10秒整轮预算；前4块相同10条文本交错执行。50次有限负载不能代替200请求验收。

- process也发生ReadTimeout，Redis不是全部模型HTTP失败的必要条件。
- HTTP客户端构建64次（含健康观测），P95=658.758ms、最大753.180ms；send至body完成69次中8次异常，含模型及健康请求。旧 `inference_ms` 包含客户端构建、网络、服务端等待及后置Redis核验，不能解释成GPU kernel时长。
- Reranker每次 `_post_uncoordinated` 新建/关闭httpx.Client；remaining/timeout计算在构建之前，构建耗时不会缩减算好的socket timeout，返回后才检查。一次重排阶段5680ms，HTTP ReadTimeout部分5021.538ms。5秒配置不是硬墙钟取消保证；这是待修复预算边界，本切片未调整。
- 压力脚本每个case新建并关闭APIEmbedding，其首次客户端构建有开销；与业务复用服务生命周期不同，不能直接等同完整业务延迟。
- HTTPcore trace只保存事件名/时间。已返回指标仅有HTTP聚合指标，没有可用的逐请求queue/compute拆分；服务端排队占比、超时后任务取消/残留仍**未验证**。未以客户端超时当作服务端停止。

## 宿主与运行时异常

`host-followup.json` 记录诊断后的观测：CPU约95%、可用内存719MB、提交内存51,731,742,720 / 53,164,290,048 bytes，PageReads/s=430、PagesInput/s=3837。另见其他项目pytest进程，未停止或归因其责任。此前瞬时GPU快照6733MiB使用/1224MiB空闲、24%利用率，不是峰值。

随后Docker exec/inspect出现Engine API 500或卡住；有界docker version探测5秒超时，两模型健康探测各1秒ReadTimeout。存在跨Redis/模型/容器管理的运行时异常。资源压力是强相关线索，尚无释放资源后的对照，不能唯一归因GPU、网络或Redis服务端。额外只读Infinity import探测发生在负载中途，也构成干扰，本次不是严格性能A/B。

未重启Docker/WSL、停止其他项目、重建候选或业务容器；未改业务配置、预算、SQL/知识库/卷，未产生供应商费用。

## 改动与验证边界

新增 `scripts/diagnose_local_admission.py`：Redis分命令计时/结果、匿名租约关联、Permit失败/close状态、HTTP客户端及协议阶段、公开模型指标与源码快照。patch只在诊断进程内有效；应用生产路径未改。`verify_gpu_pair.py`补受控Busy/Deadline错误码，BUSY仍不计成功。

真实报告源码指纹 `a1faf7cf89a91975e8ece9e82ea015ee7b4ad2f917a9f2e5502d452cc4d0f087`，起止稳定完整；报告先于新增回归及case关联名改进，不冒称最终提交已执行同一真实负载，不重写旧报告。

T0：`python scripts/test_offline.py -q tests/test_local_admission_diagnostics.py tests/test_local_admission.py tests/test_gpu_pair_acceptance.py --tb=short`，30 passed、38个既有弃用警告，2.43s，报告 `reports/offline/20261104-171425-967255`。4项新增回归覆盖匿名化、异常脱敏、释放失败不能认作删除、准入拒绝错误码与零派发；释放故障用例使用替身，真实故障链证据单独解释。

无新全量、真实多worker模型压力或完整业务验收。历史1038全量与200压力失败对应原源码，不能当作本片全量；M4失败、M5未完成、595人工待审、费用660/660保持。

## 下一切片

先确认Docker/WSL与宿主恢复健康，重测纯协调和健康端点；异常环境不继续堆模型压力。随后单独修复HTTP客户端复用与阶段预算重新计算；再单独处理释放未知的有界补偿及显式结果，不能无条件删owner或失联回退放行。补服务端逐请求排队/推理观测后重跑原200门禁；此前不切业务GPU或启用Redis业务模式。
