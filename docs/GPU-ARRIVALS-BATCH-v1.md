# 固定到达率与batch4/8候选对照 v1

2026-11-04。承接常驻200负载的并发98BUSY问题，限定诊断“健康到达率与过载”和“只改Infinity batch大小是否够用”。本片只新增评测/回滚工具，生产Python、业务配置和原验收标准未修改。

## 负载与工具

scripts/evaluate_gpu_arrivals.py按1/2/3/4/6请求每秒分别发送20条唯一查询；每条仍是Embedding1024维+20条完整长候选重排。常驻服务/同一Redis整轮准入，active1、waiters2、前台500ms、模型5秒、整轮10秒保持。两条独立预热不入100条正式分母，每组用不同query，逐条统计实际Embedding派发。

生成器至多10个在途，无隐式executor积压：槽满记录LOAD_GENERATOR_BUSY，调度迟到/worker迟到/计划至完成均保存。生成器拒绝或submit迟到P95>50ms使该固定率组无效；50ms是诊断生成器标准，不替代原产品性能标准。各组BUSY/失败/缺样本保留，不用快拒绝宣称健康吞吐。输出finite provider/model-chain scope，不是实际检索/REST/WS/SQL Run或完整M5验收。

scripts/compare_gpu_batch.py仅接受已核实owner=gpu-model-switch、loopback7999且命令完全匹配的原候选，冻结其容器ID/镜像/命令。创建owner=gpu-batch-capacity临时容器，复用相同本地镜像及只读HF缓存、CUDA/float16/OMP2/MKL2，唯一推理参数变化batch4→8；临时服务另开随机loopback端口，HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE=1禁止下载。原重排候选暂时停止，Embedding持续运行，两份重排权重不同时占用显存。finally停止临时实例，按原ID恢复原候选并核对健康及配置，即使健康验证或停止动作抛异常也执行恢复流程。没有重建/删除原容器、缓存卷或业务栈。

原HF缓存只读复用并不等于权重revision/文件hash已完整冻结；没有独立权重内容快照或排除其他缓存写入者。这是本轮复现限制，不据同模型名声称未来权重恒定。

## A/B/A结果

顺序：现有batch4(A1)→临时batch8(B)→恢复batch4(A2)。每个配置100条正式请求+2条预热，共300条正式链、6次预热；相同workload hash=e292ef22fff8a8599dbe99f6de374248a048907a340a535492d50db9133ce005，12重复语义簇，不当300独立语义质量留出。

| 到达率/秒 | batch4 A1完成 | batch8 B完成 | 恢复batch4 A2完成 | A1总P95 | B总P95 | A2总P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 20/20 | 20/20 | 20/20 | 343.980ms | 309.887ms | 332.573ms |
| 2 | 20/20 | 20/20 | 20/20 | 319.244ms | 295.080ms | 309.570ms |
| 3 | 20/20 | 20/20 | 20/20 | 319.216ms | 288.616ms | 319.217ms |
| 4 | 17/20 | 19/20 | 18/20 | 785.971ms | 753.232ms | 755.577ms |
| 6 | 13/20 | 14/20 | 12/20 | 710.374ms | 670.255ms | 783.605ms |

所有未完成均BUSY；生成器拒绝0、BUSY后部分派发0，实际Embedding派发数等于各组完成数。submit迟到P95最大2.062ms，各组生成器有效；在途峰值低负载1～2，高负载4，包含等待者，不是GPU同时推理数。

结论：本机此次短窗口中1～3请求/秒全部完成，3～4请求/秒间开始过载；20样本/档且无后台竞争，不构成可承诺长期QPS。batch8在4请求/秒较A1多2条、较A2多1条，6请求/秒仍丢6条；结果不足以证明稳定收益，更未达到无丢失。**保留batch4，不采用batch8，不提高并发/等待预算，不把容量门禁标PASS。** 阶段重排P95均<2秒，但有拒绝的组仍FAIL。

## 报告与来源

| 证据 | 路径 | 源码指纹 |
| --- | --- | --- |
| A1 | reports/gpu-pair/arrivals-20261104-191707/report.json | 159951c1df98cd581d9ac34e002f9804f2bcf1287ee1413f4a634b3e5b9feab9 |
| B及恢复外包装 | reports/gpu-pair/batch-capacity-20261104-192000/report.json | 05686f12ae34ce4fafedd15404ac782d70dece8767f5840f63c1085d5def7759 |
| B负载 | reports/gpu-pair/arrivals-20261104-192036/report.json | 同B外包装 |
| A2 | reports/gpu-pair/arrivals-20261104-192203/report.json | 81bb2156e3776d9c390510ff3224ef2f9992fae7366b68319121b4db16991fc5 |

各自source_tree_stable/source_snapshot_complete=true，分版本解释，不拼成同一源码证明。A1后新增回滚脚本/测试、独立保存source-end及故障停止条件，A2前仅将生成器测试的定时器改为确定性Event时序；未改工作负载、准入或统计公式。A1只有start清单及起止摘要，B/A2保存两份清单。最终仅将limitations中的硬编码20改为CLI样本数，真实运行均为20，不重跑已有效的实际负载。

GPU采样每轮83条，最大A1=5215MiB、B=5217MiB、A2=5177MiB；仅采样非瞬时峰值/独占显存。前后模型目录健康、未观察到OOM。B外包装original_restored_healthy=true、temporary_final_state=exited，原ID94a615ee344b09228b0b15b81a5af5b81875af126dd04252f2989fed8ae4ba03保持，原batch4命令恢复。临时aicshub-batch-capacity-76f2a65bb0e0保留但停止，未删卷；Embedding及其他项目持续运行。

## 回归与剩余范围

新增14项测试（参数化计数），覆盖非法到达率、在途上限/漏样本、异常脱敏、调度迟到、候选owner/状态/GPU/loopback守卫、命令只改batch、故障恢复及只读/离线约束。最终61相关T0通过、38条既有弃用警告、0.95秒，reports/offline/20261104-192403-954412；指纹819fe95fa534babe8f5ee8a96373a84cfc4e3806fed69e0de060b40ceae85d54，起止稳定完整、blocked_network_operations=0。compileall app scripts、切片Ruff F401/F821、git diff --check通过；不是全项目新全量。

未测：后台索引竞争、多API worker+GPU、长时稳定性、batch8独立质量/排序一致性、完整业务端点/检索、权重revision独立冻结。不同时间/加载状态、非独占宿主仍可能影响结果，单次A/B/A不足以做统计显著性承诺。

下一步应围绕既定并发10目标设计有界跨请求批处理或推理服务资源隔离的具体候选：保留每请求20候选/主体与证据来源、deadline/失租/取消、前后台公平性，并先用离线反例验证，不继续只调Infinity单个batch或放大等待时间。M4实体失败、M5质量/容量未完成、595人工复核待办和660/660额度保持；无新增付费/聊天模型调用，不进入M6、不切业务。
