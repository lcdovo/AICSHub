# 整轮本地模型准入与跨worker协调 v1

后续故障定位见 [协调 I/O、租约释放与模型等待诊断](LOCAL-ADMISSION-DIAGNOSIS-v1.md)：已复现核验/释放超时后的同owner残留与串行拒绝；HTTP等待及宿主/Docker异常独立记录，性能门禁仍未通过。

2026-11-04。OPT-12/14/16，关联AC-12-05/11/13、AC-14-05/07/09/10、AC-16-04/08局部。本切片只实现/验证模型执行资格，不能提高为M4/M5完整质量或业务部署通过。

## 行为与边界

- 检索在SQL发布快照/健康缓存检查后、首个模型调用前取得整轮资格，覆盖Embedding、召回、重排及结果核验。入队最多2、正在持有资格最多1；前台等待500ms、后台50ms；整轮资格截止10秒，模型HTTP阶段仍5秒且取父截止较小值，没有延长原整轮30秒候选。BM25纯本地CPU且不重排、外部供应商及健康缓存无需GPU准入。
- 已获资格的Embedding和Reranker共享同一Permit，HTTP不再分别竞争队列。旧BGE分批线程复制context继承Permit，Permit内部HTTP串行。后台索引每个本地Embedding批次也取得同一组资格，忙/失租继续走有界Job退避；不发布新版本、不重做业务工具。
- LOCAL_MODEL_COORDINATOR默认process，保留现有业务模式。redis模式要求API与Job worker都升级代码并连接同一Redis DB、同一LOCAL_MODEL_COORDINATOR_NAMESPACE；已升级客户端中的独立单模型入口同样参与协调。其他直接访问Ollama/Infinity的客户端不受约束。
- Redis使用原子Lua、服务器TIME、分开的前台/后台等待集合、随机owner令牌、TTL及续租，条件释放/续租不会修改新owner。默认owner TTL15秒、续租约3.75秒；进程退出后依TTL回收。前台等待优先于后台，同毫秒ticket排序不承诺严格FIFO。
- Redis模式不可用或REDIS_ENABLED=false即拒绝，不静默回退进程模式。取得资格后HTTP派发前和返回后复核owner；失租/超期拒绝结果，不再发后续模型请求。结果/错误观测不含用户正文、身份、URL或密码。
- 外层超时结束时Permit立即禁止新派发，但等已开始HTTP实际返回才释放资格；心跳在引用存续时保持。不能强杀服务端GPU任务，Redis核验与HTTP发送不是跨系统原子操作，不能宣称硬件绝不重叠。Redis数据丢失、网络分区或worker崩溃后的已发推理可能仍在服务端运行，必须保留此限制。
- 模型资格和聊天会话锁、SQL Run fence、LLM费用网关各自负责不同约束，不相互替代。无SQL模型调度迁移，无API权限/退款规则改变；业务GPU路由没有切换。

## 配置与部署

.env.example及docker-compose.yml只透传LOCAL_MODEL_COORDINATOR（默认process）和LOCAL_MODEL_COORDINATOR_NAMESPACE（默认aicshub:local-models:whole-v1）。本轮只通过docker compose config --quiet校验，未对业务执行compose up/restart或修改真实.env。namespace在Python/API/Job配置中必须相同；Redis Cluster未纳入验收。本轮真实协调验证使用m3-closeout专用Redis的随机测试namespace，不清空Redis、不操作业务键/卷。

## 分层验证

T0、真实Redis多进程、真实GPU压力三份证据独立解释：

1. 进程内嵌套整轮资格、拒绝零模型派发、提前退出仍保持在途资格、失租前/后拒绝且不释放新owner、旧BGE分批继承Permit、Redis故障关闭放行、健康缓存先返回、延迟Redis回包不准入、实际检索准入前拒绝等。
2. scripts/test_local_admission_redis.py仅核实test.owner=m3-closeout的专用Redis，两个实际子进程各5轮/每轮Embedding+重排两段合成CPU任务，间隔期间资格不释放；验证20段任务无重叠。另测旧owner不能派发/续租/释放新owner、worker退出TTL回收、活跃续租跨原TTL、Redis客户端传输故障注入时拒绝（不是停止真实Redis）。模型/聊天调用0。
3. scripts/verify_gpu_pair.py --count 200 --skip-compatibility --scheduled --whole-request --coordinator redis，将评测进程指向专用Redis随机键，再走真实GPU模型服务。原20候选/369～425字，12语义簇/200不同负载字符串；BUSY不算成功，质量595候选不用于调参。此压力主要是单评测进程10线程+真实Redis；跨进程互斥由第2份单独证明，不冒充多API进程+真实GPU的全组合压力。

业务仍默认process，新增Redis实现/真实有限验证不等于已上线Redis模式。测试结束只留新测试键的TTL或条件释放，不删业务数据。M4实体、人工595候选、真实聊天/Judge、旧索引兼容、长期故障/多实例负载及可回滚业务切换依旧独立验收。

## 本轮结果

结果在运行结束后补齐。小流程中串行2/2、配置并发10但实际仅2请求的组1成功1繁忙，繁忙前模型派发为0，整体按FAIL保留；不能拿小样本声明完成率提高。此前Redis5场景与150/158受影响回归已通过，最终源清单以最终报告为准。

## 回包丢失边界与保留的失败

在专用Redis新namespace手工复现同token两次执行ACQUIRE，旧结果为1/0（reports/gpu-pair/reply-loss-before-fix-20261104.json），不是幂等。已修复为owner==token时返回原资格；若申请命令最终异常（可能服务端已写但回包丢失），模型尚未派发，可按token条件清理占位，清理也失败时等TTL，绝不删除其他owner或退回进程放行。此修复防止协调命令重放导致自排队，不证明实际GPU故障已解决。

增强后的真实Redis7场景通过（reports/gpu-pair/redis-admission-20261104-164358/report.json），包含同token重放与成功写后注入丢回包的条件清理，另5项互斥/失租/崩溃/续租/传输故障保留。旧实现全量运行reports/offline/20261104-163715-232459在复现后主动中止，未完成、不计PASS；旧实现200压力FAIL也保留。

## 当前两轮真实GPU压力

同一200合成工作负载、随机专用Redis namespace、整轮准入，均BUSY不计成功，未切业务。

| 报告 | 并发 | 完成 | BUSY | deadline | 阶段重排P95 | 全请求总耗时P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| acceptance-20261104-163715（重放修复前） | 1 | 54 | 43 | 3 | 4550.615ms | 4189.732ms |
| acceptance-20261104-163715 | 10 | 1 | 99 | 0 | 1098.559ms | 52.220ms |
| acceptance-20261104-164455（重放修复后） | 1 | 24 | 70 | 6 | 5815.969ms | 4237.316ms |
| acceptance-20261104-164455 | 10 | 1 | 99 | 0 | 1143.443ms | 31.754ms |

两轮都FAIL，拒绝越快不代表可用性越好；忙率70～99%和部分模型超时/协调核验失效必须保留。阶段P95只对进入该模型阶段的观测计算，未进入者仍计整体未完成/总耗时，不补0；1条并发成功不足以做可靠模型P95。所有BUSY的busy_after_partial_model_dispatch均0，表示准入拒绝前未调用模型，解决了先编码后拒绝的问题，但没有解决容量/延迟。

报告分别位于reports/gpu-pair/acceptance-20261104-163715及acceptance-20261104-164455，源码各自起止稳定完整；不能将修复前后合并为400独立语义质量样本。两轮同机负载/时间不同，无独占GPU/CPU资源，不作性能因果改善承诺。一次5请求的后续diagnostic（reports/gpu-pair/redis-diagnostics-20261104.json）均完成、正常条件释放，仅用于定位正常路径，不能覆盖失败200结果。

Redis协调IO故障和模型排队各自可能影响可用性，本轮无法把压力退化唯一归因到某一个；明确保持候选/未启用状态。前台协调等待500ms、模型阶段5秒和原性能门槛没有因失败而提高。需要后续更细粒度的准入/续租/释放原因与服务排队诊断，不能仅调大超时宣称达标。

## 测试来源和未验证范围

最终功能验证保留进程模式默认，不自动开启Redis；模板仅可选透传，不执行部署。前述7项真实Redis报告与修复后200压力均为同一源码指纹9b410f32a2a597879b40aeb7e90920c9d1bb1eb2c99f5943469b35a9728da902，开始/结束稳定且快照完整。旧5场景报告是历史阶段，最终7场景额外覆盖回包丢失与重放；不能只报旧成功而忽略新增压力失败。

真实Redis故障测试为客户端传输故障注入（服务端保留真实键），没有停止Redis或模拟整机断电；多进程测试用合成CPU任务，不是多个真实API进程同时驱动GPU。跨分区GPU硬件互斥、长期连接泄漏、服务端任务取消、真实MySQL Job多worker整链以及独立质量评测均未通过。新配置Redis Cluster也未验证；只适用当前已验收的standalone Redis+同namespace协作客户端。

下一片应先补齐准入/续租/释放及推理服务的分阶段原因诊断，在固定硬件/负载下定位本轮串行BUSY与模型超时的退化；未定位前不增大等待预算、不缩候选池、不启用Redis/GPU业务路由、不扩大付费模型额度。用户人工595候选复核仍可独立继续。

## 最终封存

全量T0：python scripts/test_offline.py -q tests --tb=short，1038 passed、0失败/0错误/0跳过，577.37秒（reports/offline/20261104-164455-476590）。源码9b410f32a2a597879b40aeb7e90920c9d1bb1eb2c99f5943469b35a9728da902，开始/结束稳定且快照完整，禁止网络、blocked_network_operations=0。10038条既有datetime/依赖弃用警告保留。11项新增整轮准入回归和既有单阶段服务阻断/后台/缓存断言保留；旧两个服务共享预算用例从模拟旧HTTP锁改为模拟整轮协调器阻塞，原“两个服务都不派发/不重试”的断言完整保留，另有真实Redis7场景和全量复验，未降低拒绝标准。

compileall app scripts、切片F401/F821、docker compose config --quiet、git diff --check通过。当前测试源码与最终全量/Redis/200压力相同；文档新增不冒充模型效果验证。业务.env、GPU路由、旧模型服务、SQL/知识库和数据卷未改；原未跟踪两份规范与开发脚本保留。费用预算660/660、M4实体失败、人工标签待复核、M5完整验收不通过均保持。
