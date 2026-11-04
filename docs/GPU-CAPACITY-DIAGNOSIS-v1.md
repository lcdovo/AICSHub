# GPU候选生命周期与有限容量对照 v1

2026-11-04。前片200压力采用每case新建服务，无法代表应用常驻池。此片新增可复用服务的评测入口及隔离诊断，不改生产调度、模型/预算或业务配置。

## 工具与验证

`scripts/verify_gpu_pair.py`新增`--shared-services`（必须同时`--scheduled`），一对服务跨预热/正式组复用，线程池完成后统一关闭；原每case模式仍保留。注入服务由创建者管理，单条请求不关闭。预热查询加独立前缀，防止共享Embedding文本缓存把正式首尾样本变成缓存命中。正式200 workload及其hash不变。每case计时现在包含服务对象构造与关闭，早期构造失败也记录为失败。

`scripts/diagnose_gpu_capacity.py --count 12`先验证两个gpu-model-switch候选、loopback端口和模型目录，再使用m3-closeout专用Redis随机namespace。12份完整20候选原负载，成对比较per_case/shared，先后次序交替；shared的每条正式查询仅运行一次。另用常驻HTTP客户端执行provider并发1/2各12请求，显式绕过应用准入的诊断探针只针对本机已核实候选，不成为业务配置或容量验收。任意低并发provider失败后不自动增压，不重试失败样本。

诊断记录逐请求Embedding/重排/总耗时、绝对起止、HTTP客户端构建/关闭/TCP连接事件、GPU采样与源码指纹；不记录请求正文、URL、认证头或异常正文。HTTP阶段含网络/排队/模型，不声称GPU kernel时长或服务端取消。

T0：`reports/offline/20261104-190533-916695`，24 passed。包含5项新生命周期/异常清理/原始客户端注入/完整候选/诊断范围回归及既有验收/诊断用例。compileall app scripts、切片Ruff F401/F821与git diff --check通过。此片无生产Python改动。

## 修复前真实对照

报告`reports/gpu-pair/capacity-20261104-190545/report.json`，状态DIAGNOSTIC_COMPLETE_NOT_ACCEPTANCE。源码`eb50c6db576ca90a18a75ac420914f1cc029c893e2fa9383e8c2f65db1b19e08`，起止稳定完整；48次正式调用链均完成，另1次shared预热独立记录。

| 路径 | 并发 | 完成 | Embedding P95 | 重排P95 | 端到端P95 | 完成/秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 每case新建应用服务 | 1 | 12/12 | 1748.079ms | 1146.977ms | 2850.242ms | 0.368 |
| 常驻应用服务/Redis整轮准入 | 1 | 12/12 | 104.473ms | 340.792ms | 397.037ms | 2.888 |
| 常驻HTTP直连provider探针 | 1 | 12/12 | 37.488ms | 286.184ms | 312.416ms | 3.335 |
| 常驻HTTP直连provider探针 | 2 | 12/12 | 52.729ms | 574.263ms | 607.908ms | 3.511 |

每case路径24次HTTP客户端构造累计26078.573ms，约占该组32609.061ms的80%；shared仅预热构造2个客户端，正式请求不再构造。shared含预热共26次HTTP发送/19次TCP建连，存在连接重建，不能声称全程两个TCP。两路provider吞吐仅有限增加、P95接近翻倍，不据12条有限诊断提高业务并发上限或宣称GPU最优容量。

生产源码核实APIEmbedding仍继承环境代理且缺首次创建/关闭并发保护；这些是后续独立修复目标。不能把客户端构造的全部开销唯一归因代理，实际系统证书/宿主环境同样可能影响构造。此片未改变Embedding向量、维度、query instruction、业务旧索引或595候选标签。

下一片补Embedding客户端生命周期与本地代理边界，使用相同诊断和`--shared-services`的原200门禁复验。原D63租约补偿不替代HTTP资源管理；M4实体失败、M5质量/性能未完成、供应商660/660、rules/灰度0保持。
