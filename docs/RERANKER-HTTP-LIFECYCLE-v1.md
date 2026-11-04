# 重排 HTTP 连接复用与阶段预算 v1

后续候选健康恢复、释放补偿及原负载复验见[本地模型租约释放补偿与健康恢复](LOCAL-LEASE-RELEASE-v1.md)，不将下方历史90项结果升级为新版本全量验收。

2026-11-04。解决上一片确认的“每次重排重建HTTP客户端”和“构建客户端后仍使用旧剩余预算”问题。对应 OPT-12/14/16，关联 AC-12-05/11/13、AC-14-05/07/09/10、AC-16-04/08 的局部资源与时限约束；不代表这些断言全部或M5整体通过。

## 行为变化

- 每个重排provider懒创建并复用一个同步httpx.Client；锁保护首次创建，并发首次请求不会产生失去管理的连接池。模块单例创建/reset也使用锁。两个provider各自保留地址、认证与连接池，不混用外部和本地凭据。
- 本地已识别端点关闭环境代理继承；外部provider保留环境代理行为。未添加重试、扩大候选池或修改模型、排序与外部请求数。
- 阶段截止时间取provider预算、调用链截止和准入剩余时间的最小值。等待初始化锁及首次构建都消耗同一预算；构建后重算请求timeout，已经到期则不派发。HTTP/JSON返回后再检查，迟到结果不作为成功。
- 同步客户端构建、socket分阶段超时和JSON处理不能被此代码硬取消，因此仍不承诺严格墙钟返回或服务端任务取消。修复的是过期预算继续派发/接收的边界，没有把超时改长。
- close拒绝新调用，在途HTTP归还后再关闭连接池；反复close不会重复关闭。reset与应用lifespan退出释放单例，前一个provider清理失败仍尝试其余provider。压力脚本每条case结束也显式关闭其临时重排服务。
- Embedding已有客户端复用，本片未改其逻辑；租约释放、Redis协调、业务模型路由、缓存规则及索引空间保持原行为。旧诊断的失租/释放未知问题仍未修复。

## 验证与已有失败

T0最终命令：

```powershell
python scripts/test_offline.py -q tests/test_reranker_http_lifecycle.py tests/test_reranker.py tests/test_m5_rerank_budget.py tests/test_local_admission.py tests/test_local_inference.py tests/test_local_admission_diagnostics.py tests/test_gpu_pair_acceptance.py tests/test_m4_shutdown.py --tb=short
```

90 passed，159个既有弃用警告，7.17s。报告 `reports/offline/20261104-175117-949755`。9项新用例覆盖复用、两种构建耗时、迟到结果、并发首次构建、在途关闭、初始化锁过期、单例并发、清理失败继续关闭；原deadline用例仍断言请求剩余预算，关闭断言移至生命周期结束，没有删除断言。现有lifespan参数化用例新增实际调用重排reset的断言。

真实传输验证：`reports/http-pool/20261104/verify.py` 和 `report.json`，仅随机loopback端口的HTTP/1.1服务与合成分数。两次真实TCP请求复用同一连接，关闭后客户端is_closed=True；PASS。它是实际HTTP传输验证，**不是**真实模型、Redis、多worker压力或完整RAG验收。真实模型/付费调用均0。

上述最终T0与传输报告源码指纹均为 `300f482a4553c59736bea7d59a8725997f43d6d7baeef2ce02c5891ef126d2ac`，起止稳定完整；原88项结果为中间版本，不取代最终90项。

`compileall app scripts`通过；切片其余Python文件Ruff F401/F821通过，`app/main.py`的F821通过。main.py的F401仍有第6行原有未使用uuid导入，已用HEAD原文件核实同样失败，保留并报告，未借本片清理无关代码。`git diff --check`通过。

## 当前运行与未验证范围

本片开始时Docker管理接口已能返回版本29.7.2，但两个GPU端点ConnectTimeout；进一步只读inspect确认两个owner=gpu-model-switch候选均exited。未自动启动、重建、重启模型或其他项目，未执行新GPU压力，也未宣称宿主已全面恢复健康。无业务.env/部署/数据库/知识库/卷变更，三个原未跟踪文件保留。

未验证：客户端复用后的真实模型耗时改善、真实模型长期连接稳定性、多worker模型压力、服务端排队/取消和原200请求门禁。未运行本片全量回归。旧200请求FAIL、M4失败、M5未完成、人工595候选待审及660/660付费额度不变。

下一片先检查宿主资源并恢复已授权候选的健康基线，再做相同负载的真实模型验证；随后单独处理租约释放未知的有界条件补偿及可观测性，不用放大超时或删owner掩盖失败。
