# M2 实施契约 v1

## SQL Run与协调（OPT-05/15）

Run由owner/session/client_message_id唯一约束去重，摘要覆盖actor与精确message/action参数。相同参数返回原Run，变化409。旧客户端不带client_message_id时服务器生成独立ID，只保证本次执行，不承诺旧客户端任意重试去重。

REST与WS共用run_execution执行器；ChatService保持同一图与最终校验。accepted事务先记录Run和stream_start；取得Redis/SQL协调资格并再次认证后，提交唯一用户消息。SQL会话租约45秒、每10秒续租，单轮执行30秒有界，进程内最多64个活动Run。Redis不可用时使用已验收SQL条件租约；原Redis已获得后失租必须中止，写工具在业务事务内重查SQLfence及记录的Redis令牌。

最终消息、Run终态、最终token/done/stream_end及摘要Job在同一SQL事务提交，随后传输/缓存。与M1相比done不再表示未落库的文本。WS按Run独立执行，断线只停止订阅，不推断取消。每事件带schema_version/run/session/event_id/sequence/time；每Run最多64条阶段进度，最终文本一次发布；图队列64、WS发送5秒超时，慢客户端不会取消已接受执行。

GET /api/v1/chat/runs/{id}、/events?after=sequence只允许owner/admin；GET /api/v1/chat/sessions/{session}/runs/{client_message_id}用于首个回包也丢失时解析原Run。前端生成client_message_id，按Run/sequence拒绝重复、乱序补齐；断线只GET状态与事件，页面刷新使用sessionStorage中的Run或客户端ID恢复，绝不再次POST原业务消息。

POST /runs/{id}/cancel仅取消accepted或awaiting_confirmation；运行中/已完成409。等待确认只取消该Run引用的预览，不取消会话其他动作。显式关闭两入口均递增SQL版本并清除租约；删除改为tombstone、隐藏列表/历史与记忆，不物理删除审计/动作回执/已提交Run。保留数据的清理策略不在本次自动执行范围。

## 恢复与投影

工具提交后最终事务失败/进程中止：Run先reconciling，恢复扫描只按源消息幂等键/动作引用读取原SQL回执并补齐同Run最终消息。恢复文本仍经响应节点独立校验；已提交退款仅清除对应action的待确认SQL投影。没有证明回执则明确failed/interrupted_unknown，不自动重做图或模型。旧持有者不能提交新版本，恢复扫描也不抢其他有效SQL租约。关闭/删除时取消孤儿Run，回执独立保留。

SQL会话version/context_data/summary_cursor是事实；每轮从SQL有界读取40条未覆盖消息及待确认动作，原始全部历史仍在SQL。摘要采用规则投影rules-v1、记录覆盖到message_id及summary_version，独立Job计算，不调用付费模型。摘要/缓存失败不改变已提交Run结果。

Redis新投影使用session_state:v2:{id}、schema2与session_version，通过Lua CAS拒绝旧/相同版本；旧session:{id}仅验证读取，错误内容保留并由SQL恢复，不把错误缓存当新会话。旧锁键保持兼容，不允许新旧worker在迁移期间并行写。应用内状态/摘要容量各1024，锁注册表在使用后回收。长期记忆由owner SQL联查且过滤tombstone，带message/session/source标识，仅作历史用户陈述，不作为本轮确认/当前订单事实。

## Job/Outbox（OPT-16基础）

业务事务同提交NotificationRecord与通知Job，历史Outbox通过幂等import_outbox补任务引用。Job只含资源ID/owner/kind，不复制正文/收件地址/密钥。事件键唯一、不同事件不误去重。worker以SQL条件更新抢占与租约令牌确认；attempt默认3，失败指数退避最多300秒，最后尝试崩溃也收敛failed；管理员可查和审计重试，普通用户禁止重放任务。

独立入口python scripts/run_worker.py [--once] [--kind summary|notification] [--simulate]：每次最多10个任务顺序消费；summary与notification可分别启动进程，长任务不在web请求执行。未注册索引处理器明确handler_not_registered，不假装索引已恢复；真实解析/索引算法留M3。

无渠道配置：failed/channel_unavailable，不构造sent。--simulate使用独立simulated_deliveries回执，is_simulated=true，不发送外部通知。适配器必须提供lookup/send，以事件ID幂等；先sending提交，再投递，sent必须有provider_receipt。发送后确认丢失只lookup：能查到回执则确认；查不到保持unknown/delivery_unknown，不再次发送；管理重试也不能绕过对账。这里不声称外部恰好一次、不等于用户已读。

## 迁移与边界

0008_m2_runs_jobs新增SQL版本/游标/租约/删除标记，messages.run_id可空，Run/事件/Job/模拟回执及通知状态。旧消息和通知保留，历史消息不生成伪Run；历史待发通知只有显式worker才处理。迁移仅在隔离MySQL演练，生产升级需备份、迁移后完整切换worker；拒绝有损自动降级。

未调用真实LLM/embedding/reranker、支付/短信/邮件；未迁移业务库、未部署、未推送。真实依赖验证与替身故障分开记录，验收结论见M2-EVIDENCE-v1.md。
