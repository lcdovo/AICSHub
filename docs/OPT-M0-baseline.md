# M0 基线与首个切片契约（2026-11-03）

本记录补充《整体优化方案 v1》《优化验收规范 v1》，以当前源码和本次执行为准。
M0 的离线检查、隔离基线和代表性反例已完成；真实依赖基线、数据备份恢复、真实模型质量/成本仍为 NOT_RUN，不能视为完整阶段验收或发布许可。

## 工作区与资源

- 原始 HEAD：`2c2e5b0655262689f3bd7278a827b872f90b5a2d`，无已跟踪文件改动。
- 原有未跟踪文件：`docs/整体优化方案-v1.md`、`docs/优化验收规范-v1.md`、`scripts/_dev_run_import.py`。保持内容和未跟踪状态，不夹带提交。
- Python 3.13.9、pytest 8.3.3；其余执行依赖版本见各次 manifest。使用现有安装环境，未重装依赖；没有证明从 lock 文件可重建。
- `python scripts/test_offline.py`：T0 专用入口。先清除 Settings 同名环境变量，再切到新临时目录（不读项目 `.env`），使用临时 SQLite，禁用 Redis/Milvus/Langfuse/演示账号及长期记忆；进程审计钩子阻断 DNS、网络连接和 UDP 发送。仅放行标准库 socketpair 内部连接，供 Windows asyncio 唤醒使用；未开放任意回环连接。
- 固定合成 fixture；pytest 临时目录与本次进程临时目录绑定。未启动主应用生命周期、未迁移或访问个人业务库，未调用外部支付/通知/模型，未启动或停止容器，未操作数据卷。
- 未执行现有业务数据备份或恢复；本切片不修改数据结构/业务数据，备份恢复门禁保留到独立 T1/T4 验收，不能宣称已备份。
- T0 外部调用预算冻结为 **0 次 / 0 外部费用**。供应商单价未核实；方案中“先记录30条真实样本”的成本冻结尚未执行，真实调用前需另定费用上限。
- 前端 build 输出改到忽略目录 `reports/offline/frontend-build`，保留原 `static/`。

## 已执行基线（修复前）

| 范围 | 实际结果 | 原始证据（本机 reports，Git 忽略） |
| --- | --- | --- |
| 全部原有 pytest，T0 | **162 PASS / 2 FAIL**，0 skip/xfail；退出1，阻断网络操作0 | `reports/offline/20261103-130513-670026/`：pytest.log、junit.xml、manifest.json |
| 新退款契约13例 + E04/E06临时探针2例，T0 | **1 PASS / 14 FAIL**，退出1 | `reports/offline/20261103-130707-468332/` |
| `frontend: npm test -- --reporter=dot` | 5文件、21 PASS，退出0 | `reports/offline/frontend-baseline.log` |
| `frontend: npm run build -- --outDir ../reports/offline/frontend-build` | TypeScript + Vite通过，退出0 | `reports/offline/frontend-build.log` |

不叠加这些重叠集合。最初两次离线入口试跑因 Tee 缺少 isatty、Windows socketpair 被误阻断而无有效基线；已修复入口，不归因于应用，不删除失败日志。

已有失败不修改断言、不标 skip：

1. `tests/test_intent.py::test_low_confidence_keeps_llm_fallback`：对“退”期待模型兜底；当前结果是澄清且不调用模型。与同文件澄清用例存在策略契约冲突。保留 FAIL，后续 OPT-06/AC-06-07 冻结策略再处理。
2. `tests/test_query_rewrite.py::test_no_previous_context_calls_llm`：期待无上文时调用模型1次；`ConservativeQueryRewriter` 返回 `simple=True`，实际0次。新发现，记 E26，关联 OPT-06/12，后续明确无上下文改写契约。

pytest 另有 asyncio fixture scope、UTC 时间和 multipart 弃用警告，未修改无关逻辑消除警告。

## 问题核实台账

R=本次合成复现，S=本次源码核对，V=真实环境待验证。原方案中的历史 R 不是本轮自动通过的证据。

| 问题 | 本轮等级与结论 | owning source / 未验证范围 |
| --- | --- | --- |
| E01 | S，动作工具生成ID/成功，退款未持久化 | `app/agent/tools.py`；四类完整闭环未验收 |
| E02 | R/S，工厂/进入会话/查询/退出异常，旧示例号仍 success=true；普通号缺错误分类 | `tests/test_refund_dependency.py`；首个修复目标 |
| E03 | S，内存协同与数据库API并存 | `app/services/collaboration.py`、`app/routers/tickets.py`；真实人工系统未接入验证 |
| E04 | R/S，B(202)已有键，A(101)收到B工单 | `app/routers/tickets.py`；临时探针在 `reports/offline/test_m0_probes.py` |
| E05 | S，WS仅解码身份，缺REST用户状态/版本等价校验；关闭会话会重开 | `app/routers/chat.py`、`app/security.py`；撤销/多worker真实验收未做 |
| E06 | R/S，创建路径存整数，反序列化对整数调用get报错 | `app/routers/sessions.py`、`app/agent/memory.py`；临时探针同上，真实Redis未测 |
| E07 | S，代建路径使用current_user.id | `app/routers/sessions.py`；代建API合成集成未做 |
| E08 | S，快照缺失新建空轮次；DB commit后单独save_state | `app/services/chat_service.py`；崩溃窗口未测 |
| E09/E10 | S，规则/模型输入及历史路由待修 | `app/agent/intent.py`、`app/agent/nodes/intent_node.py`、`app/services/llm_service.py`；未跑新模型留出集 |
| E11/E12 | S，无证据事实分1，子串检查无法可靠排除状态否定/追加承诺，诈骗敏感词 | `app/agent/validation.py`；本轮未重跑全部审查反例 |
| E13 | S，embedding为None提前返回，BM25未执行 | `app/services/knowledge_base/retrieval.py`；真实故障未测 |
| E14 | S，融合分与final_score及固定门槛混用 | `retrieval.py`、`app/agent/nodes/rag_node.py`；真实检索分布未测 |
| E15 | S，hybrid异常后纯向量仍标hybrid_milvus | `retrieval.py::_search_milvus`；真实索引故障未测 |
| E16 | V，Milvus索引/版本契约仍待真实核验 | `app/utils/milvus_client.py` |
| E17/E18 | S，入库/恢复ID、MySQL删除范围、标题切块与证据前缀截断需修 | `ingestion.py`、`index_admin.py`、`response_node.py`、`validation_llm.py`；历史数据迁移未测 |
| E19 | S，空向量补零及维度修整仍存在 | `app/services/embedding_service.py`；供应商真实契约未测 |
| E20 | V，多worker知识缓存传播/线程共享待测 | `cache_invalidation.py`、`knowledge_base/__init__.py` |
| E21 | S/V，结构化调用路径与预算check/consume未统一 | `llm_service.py`、`llm_governance.py`；并发预留未测 |
| E22 | S，MCP异常自动直调 | `app/agent/mcp_client.py`；响应丢失/远端已执行未测 |
| E23 | S，缺持久Run/客户端消息去重 | `app/routers/chat.py`、`app/services/chat_service.py`；恢复未测 |
| E24 | S，Trace success固定true、每节点重复轮次token | `app/services/chat_service.py`；真实计量未核验 |
| E25 | S，旧文档命令/历史数字不能当本次验收 | 以本报告当前命令/结果为准，未全面改写历史文档 |
| E26 | R/S，缺上文改写的测试与simple短路冲突 | 见上述既有失败2 |

## 冻结首个 M1 切片：退款数据库故障封闭失败

- 选择依据：P0，数据库不可用时直接绕过归属/资格并构造成功；无需迁移、模型或新领域服务，可独立阻断。工单跨用户幂等同属P0，但完整修复需覆盖作用域、请求摘要、唯一约束与并发，不在本切片顺带修改。
- 对应 **OPT-08/OPT-09、E02；AC-09-02、AC-08-02 的退款子集**。AC-09-01仅覆盖缺失/他人订单负向查询，不宣称整个AC通过。
- 契约：退款订单查询任一数据库边界异常，返回 `success=false`、`error_type=dependency_unavailable`、`retryable=false`，不包含申请ID/金额/待审状态，不缓存失败。错误不透传异常内容，客户端可稍后重新发起；通用包装不自动重试该失败。
- 影响：原先数据库故障时的示例退款成功变为明确失败。正常数据库查询与已有业务拒绝路径保留；本切片不重定义资格规则。
- 未解决：正常查单后的退款成功仍没有真实申请记录（E01）、确认action/权限类型/MCP重放/持久幂等仍待后续。不得将本切片记为M1整体通过。
- 行为词典：`success=true` 应来自真实回执是目标，当前工具仍存在偏差；`dependency_unavailable` 只代表未完成资格校验，不能表述为“退款已提交”；`pending_review` 不等于到账。原规范所有硬门禁保持不变。

## 原文档适用范围更正

验收规范2.2第9条“本次交付仅新增文档”描述的是先前文档交付；当前用户已授权M0与一个M1代码切片，以本次范围为准。原方案“尚未实施”及旧测试数字是历史快照，实施状态从本报告及后续切片报告追踪。没有更改或降低任何AC断言。

下一切片建议：工单/转人工幂等与资源归属（OPT-04/10，优先AC-04-03，再同参数并发/参数冲突AC-04-04），另行评估迁移与审计范围。
