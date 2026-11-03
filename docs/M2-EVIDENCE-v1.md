# M2恢复与执行验收证据 v1

日期2026-11-03。冻结范围见M2-CLOSEOUT-v1.md：OPT-05/15/16基础，20项P0及11项支撑P1，共31项。基线789ae38；未扩大到M3索引算法、真实通知或生产部署。

## 交付与行为

1. SQL Run/RunEvent与会话version/context/summary_cursor、客户端消息唯一键、统一REST/WS执行。相同消息ID返回原Run，变化409；最终消息/Run/事件/摘要任务同事务提交后发布。影响聊天重试、完成事件时机、缓存失效与恢复（OPT-05/15）。
2. SQL租约fencing、独立执行、回执恢复/孤儿扫描、tombstone、历史记忆来源、有界缓存。Redis获取前不可用走SQL后备，取得后失租停止提交；原副作用只查回执，结果未知不重放。影响断线、重启、关闭删除、租约丢失、退款确认（OPT-05/15）。
3. 持久Job/Outbox、独立worker、有限重试与固定错误码、模拟回执及unknown先对账、管理员审计重试。影响工单/人工通知、规则摘要；不产生真实通知，不宣称外部恰好一次（OPT-16）。
4. 前端生成client_message_id，保存原Run/客户端引用；按session/run/sequence去重与有界乱序恢复，断线只GET，不再次POST业务消息（OPT-15）。

完整接口、租约、迁移与渠道契约见M2-IMPLEMENTATION-v1.md。

## 证据层次与命令

T0：`python scripts/test_offline.py -q --tb=short tests`，临时工作目录、合成SQLite、禁网络，521 passed，0 failed/skip，335.40秒，退出码0。原始JUnit/log/配置指纹与依赖版本：reports/offline/20261103-170056-707628。随后追加回归（事务回滚不留Job、退款回执恢复、摘要缓存上限）与补强慢连接/真实提交失败断言；追加验证均单独记录，不能把521说成最新单轮通过数。

T0追加：M2三文件25 passed（reports/offline/20261103-170358-391526）；退款回执1 passed（170525-175206）；Run补强10 passed（170647-814158）。最后退款恢复投影修正后67项聊天/退款/Run及4项上下文通过（170801-789008、170947-921043），退出码0。

T1/F：`python scripts/test_m2_real.py`，16 passed，82.98秒，退出码0，reports/m2-closeout/real-20261103-165914。真实专属MySQL/Redis、实际spawn进程、每例新建合成库，容器label=aicshub.test.owner:m2-closeout。追加并发上下文与旧schema新表迁移加强3 passed（real-20261103-170637）；最终恢复修正后的崩溃/实际图复验5 passed（real-20261103-170812），退出码0。

- 两个实际进程同ID竞争，有/无Redis各20轮，各保持20Run/20工单/40消息/会话version20，后轮恢复前轮消息。
- 实际os._exit覆盖用户消息后、业务提交后、最终事务前、最终事务后四个窗口；恢复重复执行三次不增加动作/消息。
- 两任务worker竞争20事件只产生20个独立模拟回执；发送后确认前实际进程退出，恢复读取原回执，无第二次投递。
- 实际Redis20轮旧版本CAS被拒绝；专用Redis重启后只删除指定合成投影键，SQL恢复历史/待确认动作，不用FLUSHDB。
- 20轮Redis令牌被替换后旧工具拒绝提交且保留新锁；20轮实际迟到worker与恢复扫描/关闭/tombstone协作不复活。
- 摘要worker实际退出后接管推进cursor；未注册索引明确failed。独立进程占用合成长索引任务槽时，另一聊天5秒内完成；这验证M2进程/有界消费基础，不代表M3实际解析CPU/内存压力验收。

T1故障/竞争通过替换图入口为确定性合成服务，业务工具、SQL/租约/最终事务真实；不能将这类测试称作完整真实图。另一个test_actual_graph_rest_ws_refund_and_m1_safety_regression保留实际图与最终validator，覆盖REST/WS和M1退款边界；模型关闭，使用模板/明确降级。

B：`npm test -- --run`，6文件26 passed；`npm run build -- --outDir ../reports/m2-closeout/frontend-build`，TypeScript/Vite通过，退出码0。生产构建的真实浏览器合成账户：退款预览执行中刷新→SQL待确认恢复→明确确认时刷新→单一0.10 CNY待审模拟申请。截图/AX：reports/m2-closeout/browser/refund-after-refresh.png与.txt；原申请042f2b8b-dd46-497d-aead-bf582f7768ff。先前控制台error为空。

验收服务实际进程49408→51700重启后，真实SQL及已认证GET核对3Run/6消息/1申请、原message_id/回执/历史保持不变；OpenAPI可读取，restart-check.json保存结果。最后重启后的浏览器重连工具报“browser.user is unavailable”，没有把失败尝试列为浏览器通过；改用真实HTTP/SQL复验。前端最终类型修复已通过构建，重启后的最终浏览器重验未执行。

C：`python -m compileall -q app scripts acceptance/m2`、`git diff --check`通过。源码/合成测试数据定义/锁定依赖/构建/原始JUnit的SHA256与命令退出码见M2-GATES-v1.json及忽略的reports/m2-closeout/source-sha256.json。早期日志HEAD为冻结提交，实际验证含工作区变更；不能将其冒充该历史HEAD的干净构建，最终源码提交与哈希另行记录。

## 逐项验收

以下PASS均限冻结M2基础及所列实际层次，原验收断言未降低。T0测试使用替身的部分不证明真实供应商可用。

| AC | 证据（文件/用例简名） | 结论 |
| --- | --- | --- |
| 05-01 | T0 test_chat_turns跨轮清理；test_m2_context空SQL投影不复活旧缓存参数 | PASS |
| 05-02 | T0 test_session_creation两入口即时REST/WS；T1实际图创建续聊 | PASS |
| 05-03 | T1 real_sql_context_recovers_after_owned_redis_restart指定键丢失后恢复历史/action，无确认标记 | PASS |
| 05-04 | T1四进程退出窗口/Redis重启/摘要退出；B刷新与真实进程重启后HTTP/SQL同回执 | PASS |
| 05-05 | T0 summary_cursor_is_monotonic_and_resumes_without_recount，50条分两次游标30→40、策略版本 | PASS |
| 05-06 | T1有/无Redis实际双worker各20轮读前轮历史；Redis20轮迟到投影CAS | PASS |
| 05-07 | T1 real_redis_lease_loss_blocks_tool_and_keeps_replacement_lock，20轮；旧任务ack拒绝 | PASS |
| 05-08 | T1 Redis关闭配置下双进程20轮SQL后备；单进程T0路径；不扩展到旧入口 | PASS |
| 05-09 | T0 memory_owner_source_and_tombstone_not_current_business_fact、M1历史注入与本轮确认约束；历史陈述标记 | PASS |
| 05-10 | T0 local_state_cache_and_lock_registry_are_bounded，1100会话、state<=1024、锁回收；摘要缓存上限独立检查 | PASS |
| 15-01 | T1双worker同ID20轮；T0 client_id_replay_conflict_authorization_and_event_cursor，同参数原Run/改参数409 | PASS |
| 15-02 | T0事件schema/session/run/sequence单调/游标，真实故障重复恢复终态不倒退 | PASS |
| 15-03 | T1 actual_process_crash_and_repeated_recovery[after_user]，1用户消息、0动作、明确failed | PASS |
| 15-04 | T1 [after_tool/before_final]原工单；T0 refund_committed_receipt_recovery原申请、金额、待确认标记清理 | PASS |
| 15-05 | T1 [after_final]保持原result；T0游标重放；B确认中刷新单一回执 | PASS |
| 15-06 | T0 final_commit_failure_never_publishes_completed_event，实际AsyncSession.commit故障、flush后回滚无最终事件 | PASS |
| 15-07 | 前端transport.test重复/乱序/错误Run与断线GET恢复；T0旧游标；B刷新 | PASS |
| 15-08 | B确认处理中刷新；T0慢发送超时不取消独立Run；T1动作提交后崩溃只对账 | PASS |
| 15-09 | T0 cancel_waiting_run_only_cancels_its_action、cancelled_accepted_run_cannot_write_user_message | PASS |
| 15-10 | T1 late_actual_worker_recovery_scan_and_tombstone_20_rounds；T0关闭入口回归与已删查询隔离 | PASS |
| 15-11 | 图队列64、进度64、发送5秒、前端缓冲100；T0慢socket超时后原Run与另一用户均完成 | PASS（有界合成负载） |
| 15-12 | T1迟到worker/替换有效租约/扫描不偷新租约20轮，无未知写重放 | PASS |
| 16-01 | T0 business_rollback_has_no_consumable_job及business_outbox_and_job_share_commit；真实T1业务/任务持久化 | PASS |
| 16-02 | T1 actual_worker_dies_after_delivery_before_ack；模拟供应商独立唯一回执，实际进程崩溃后只lookup | PASS（模拟渠道） |
| 16-03 | T1 two_actual_job_workers_20_events；T0过期租约旧ack拒绝 | PASS |
| 16-04 | T0 repeated_dependency_failure_backoff_and_last_crash_converge、权限审计重试；3尝试2/4/8秒退避与failed | PASS |
| 16-05 | T0 missing/simulated/provider_substitute契约与unknown无证明不重发；不是实际外部通知 | PASS |
| 16-06 | T0 same_event_dedup_preserves_distinct_events_for_same_resource及历史Outbox幂等import | PASS |
| 16-07 | T1 summary_job_survives_actual_worker_crash，摘要接管、索引未注册明确失败；M3算法不纳入 | PASS（基础契约） |
| 16-08 | T1 long_background_process_does_not_block_chat，实际独立进程/SQL、有界消费，合成长索引处理器 | PASS（基础预算） |
| 16-09 | T0 payload仅引用/异常正文不入库、job_retry_and_notification_owner_permissions，普通用户不能管理重试 | PASS |

## 保留的失败和未执行范围

早期受影响测试2个退款用例失败：新增业务Redis租约检查未接到测试fixture的同一Redis替身。修正fixture连接，不删除原断言，19项退款随后通过。前端首次构建可选ID类型错误，修复后26项及构建通过。真实验收早期NameError=subprocess是测试代码块位置错误，原失败日志保留real-20261103-160704，修正后重新通过。未skip/xfail或降验收标准。

NOT_RUN：本轮真实LLM/embedding/reranker调用、真实邮件/短信/支付、Milvus索引算法/解析性能、生产业务库迁移、生产部署/备份恢复演练与长期负载。此前M1仅4次模型授权已用完，本轮未追加费用调用。真实供应商送达/已读/外部exactly-once不属于通过结论。最后一次浏览器工具故障后的重启页面未重验，接口与SQL已验证。

原业务库/知识库/卷未清空或迁移；没有推送。原未跟踪整体优化方案、验收规范及scripts/_dev_run_import.py原样保留。新增0008仅在合成库演练；生产实施仍需单独授权。

## 提交与停止点

23b2ef9固定范围；e91b16e Run/SQL会话恢复；364f71b Job/Outbox；1f3f72f前端恢复；c2d83f7真实依赖验收。61323a7修正已提交退款的旧待确认投影。最终文档封存提交由Git历史及最终报告提供。修改文件见M2-FILES-v1.md。达到冻结31项后停止，下一阶段建议M3先修稳定文档/版本ID与索引生命周期，不自动继续。
