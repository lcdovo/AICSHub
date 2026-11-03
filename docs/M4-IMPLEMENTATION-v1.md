# M4候选实现 v1

2026-11-04。冻结范围为OPT-06/07/14共30项，当前状态 **未完成验收**，实际业务默认rules、灰度0。原验收门槛保持不变。

| 切片 | 解决问题及行为 | OPT / AC | 主要文件 |
| --- | --- | --- | --- |
| SQL模型网关 | 每次实际供应商尝试先预留日tokens/估算费用、共享SQL槽；Run上限6尝试、共同费用与deadline；失败保留未知计量 | OPT-14 / AC-14-01～10 | model_gateway.py、llm_service.py、models.py、0010/0011、model_calls.py |
| 请求理解与图边界 | 严格结构化topic/type/entities/conditions；咨询不走申请；条件先读；无效至多修复一次后澄清；五类类型隔离、每轮清空、步数24 | OPT-06 / AC-06-01～12；OPT-07 / AC-07-01～08 | request_understanding.py、state.py、intent_node.py、graph.py |
| 策略固化 | Run接收时固定rules/shadow/model及版本；重连复用；shadow禁止确认/取消写入 | OPT-06/14 / AC-06-12、AC-14-08 | run_execution.py、chat_service.py |
| 受控只读计划 | 最多2个可信reader白名单任务并行，不接受user_id、SQL、代码；暂未接主图，不冒充完整多诉求执行 | OPT-07 / AC-07-05/06 | read_plan.py |
| 真实验证工具 | 每次HTTP持久扣授权预算、无自动重试；独立场景/初始矩阵分别存储，错误留存 | OPT-06/14 / AC-06-11、AC-14-02/04/07/09 | evaluate_m4_live.py、test_m4_real.py、acceptance/m4/ |

数据库新增0010模型日预算/调用/并发槽与0011模型revision/价格快照；历史未知不补造版本。业务数据库已从0004升级0011，升级前有冷卷和逻辑备份。API新增管理员模型计量查询，不存prompt/正文/凭据。

LLM_GATEWAY_ENABLED为兼容默认false；当前业务显式true。旧直接调用没有Run上下文时使用system user0和direct标识，不能当作终端用户身份完整覆盖。ModelCallRecord是审计账本；用户界面的旧模型摘要不替代它。价格为配置估算，实际usage与真实账单分开。

结构化schema新增严格边界，但不是全图类型重构。collected_info仍存在。只读计划尚未接入主图，多诉求不自动合并执行；类型化字段不能提升业务权限。退款资格、权限、参数、幂等与本轮精确确认仍由已有共享业务服务强制。

真实独立评测后修复了过宽的否定词防线（例如“退款，不要维修”不能误拒）并补7个回归。该修复尚未进行新的300条真实独立留出评测。流式网关追加剩余deadline约束与迟到流回归；不增加供应商调用。

逐项状态和缺口以M4-GATES-v1.json、M4-EVIDENCE-v1.md为准；有功能和有测试不等于30项完整通过。
