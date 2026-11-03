# M1 固定收尾验收证据 v1

日期2026-11-03；范围固定为M1-CLOSEOUT-v1.md列出的35项P0，不扩到M2～M7。基线451通过/2失败；历史失败保留原断言修复，未skip/xfail。

## 本次行为与分包

- R1/R2（OPT-09、OPT-03）：SQL持久退款预览、精确版本确认、取消/期限、多动作澄清、全额CNY/签收7天、剩余额度、跨会话去重和模拟审核；审核不支付。订单金额迁移到Numeric(12,2)。影响退款入口、REST/WS恢复和回执发布顺序。
- R3/R4（OPT-04/08）：私有工具读取绑定可信actor和参数，每次核对当前身份；写结果未知只读回执，严格MCP类型、缺工具/缺回执失败。影响直接/MCP读取与错误边界。Trace、日志、评测副本脱敏，业务校验仍使用原证据。
- R5：模糊意图和无历史改写按原测试契约保留兜底机会；无真实模型调用。
- R6/R7/R8：真实MySQL/Redis、多进程/重启/回包故障、真实stdio、生产前端浏览器操作、逐项证据映射；测试仅合成数据。

## 验证层次

T0：scripts/test_offline.py切换临时工作目录、隔离SQLite、禁网络；真实ASGI/业务服务/SQL但部分图/模型/Redis替身。不能用T0宣称真实MySQL/模型通过。

T1：scripts/test_m1_real.py核对专有容器label，独立MySQL/Redis、每例新库。未替换图或最终validator的真实REST/WS流程；模型关闭，采用业务模板/明确降级。回包丢失测试只注入响应故障，实际提交及只读恢复用MySQL。

L：真实deepseek-flash供应商4次合成调用、每次最多256输出tokens、thinking disabled；4/4通过，实际总用量见忽略的model/injection.json。该有限提示注入烟测不证明长期/任意输入安全；服务端身份与确认独立强制约束。没有额外调用。

B：本次浏览器工具操作生产Vite构建（非dev server），合成账户、独立MySQL/Redis，未使用模型或embedding替身，也未调用外部模型。证据在reports/m1-closeout/browser。客户预览0.10 CNY → 确认原action版本 → 单一待审回执；第二订单取消不创建申请。实际服务进程24552→35332→34556，原申请和聊天工单保持ID/归属/内容。管理员审核显示“模拟审核通过（未支付）”；第二用户访问客户会话显示权限错误，不出现客户内容。工单9710c6c1-1d15-42b2-94a2-a1e0d3017fc6由聊天创建，重启后管理员读取owner202及原描述。控制台最后检查无error；全页截图API曾失败，已用可用的视口截图保留证据。

浏览器截图：admin-reviewed.png、customer-submitted.png、foreign-denied.png、ticket-after-restart.png；同时保存关键AX文本。未以浏览器网络mock替换接口。

## 逐项断言与证据定位

下表文件均为仓库相对路径，命令结果由最终门禁记录指向原始JUnit/log；“I”包括真实ASGI/SQL集成，并明确SQLite与MySQL层。每项原断言仍以冻结清单为准。

| AC | 证据与实际层次 | 结论 |
| --- | --- | --- |
| 03-01 | T1 real_services.py::test_mysql_real_rest_ws_and_refund_review：REST/WS咨询、查单、工单、退款独立fixture结果成对比较；T0 test_chat_turns.py | PASS |
| 03-02 | T0 test_session_creation.py全部兼容入口+即时WS；T1 real_redis_snapshot_and_lock及真实REST/WS会话 | PASS |
| 03-03 | T0 test_session_creation.py::test_admin_creates_for_owner_with_actor_audit，两入口actor999/owner101 | PASS |
| 04-01 | T1 existing_resource_identity_matrix_and_revocation现存会话/消息历史/资料/工单/人工/退款/动作/Trace证据/反馈；T0既有owner矩阵；B foreign-denied | PASS |
| 04-02 | T0 test_m1_read_tools.py、ticket/refund严格参数及签名测试；T1 injected_evidence与stdio拒绝裸user_id | PASS |
| 04-03 | T0 test_ticket_idempotency.py::test_cross_owner_key_never_returns_foreign_receipt，工单/人工两类 | PASS |
| 04-04 | T0 changed_payload/ten_concurrent_requests与commit_failure；T1退款20轮两进程 | PASS |
| 04-05 | T0 test_chat_auth.py禁用/删除/disabled_at/版本/到期REST与新WS及开放WS下一轮；T1版本撤销401 | PASS |
| 04-06 | T0 admin_role_is_read_from_database及admin_demotion_during_rest_lock_wait；T1降权管理员403；私有工具实时角色拒绝 | PASS |
| 04-07 | T0 invalid_token_contract/expiry/refresh_rotation/revoked_refresh/disabled_at_refresh，真实JWT+SQL+HTTP/WS，图替身 | PASS |
| 04-09 | T1 injected_evidence_cannot_change_identity_or_confirm（文档/历史/工具字段注入）；L四次供应商合成输入4/4 | PASS（限定样本） |
| 04-11 | T0 test_m1_privacy.py：嵌套日志、OTel观察器前脱敏、真实SQL Trace/评测副本，金额/资源ID不损坏 | PASS |
| 08-01 | T1真实stdio创建并SQL读取；T0 ticket/refund/update/handoff的inprocess MCP与HTTP同表测试；B工单详情 | PASS |
| 08-02 | T0 refund_dependency/ticket_tool/update_tool/handoff_persistence及notification_unavailable，依赖错误不伪造ID | PASS |
| 08-03 | T0严格Pydantic写工具矩阵；T1 stdio字符串/布尔/浮点身份被协议拒绝 | PASS |
| 08-05 | T1 stdio重复同键、退款新进程回执重放；T0提交前后丢回包、并发与原回执一致 | PASS |
| 08-06 | T1 refund_lost_result_only_reads_committed_receipt；T0 m1_tool_contracts证明direct未调用及未知保持unknown | PASS |
| 08-08 | T0未知工具/缺业务回执明确失败；T1 stdio missing-tool为isError | PASS |
| 09-01 | T0 refund_persistence owner/actor/query真实JWT与SQL；T1 owner矩阵B订单不创建申请 | PASS |
| 09-02 | T0 refund_dependency故障不生成ID/不返回硬编码299；T1 lost_result只读恢复 | PASS |
| 09-03 | T0 m1_refund_actions签收缺失/未来/8天/已退余额；T1真实签收批准及额外amount422 | PASS |
| 09-04 | T1 MySQL0.10→动作/申请/回执；B显示0.10 CNY；旧Float99.10迁移保留；T0金额变化拒绝 | PASS |
| 09-05 | T1预览不写申请+真实图确认；B精确金额、版本、期限与明确按钮 | PASS |
| 09-06 | T0历史确认/取消持久化；T1取消后确认拒绝；B取消后只剩原申请 | PASS |
| 09-07 | T0 expiry/version/order/type/reason/amount；T1重新预览后旧版本、到期拒绝 | PASS |
| 09-08 | T1两个SQL待确认动作后只说确认，回复多个目标澄清且无申请 | PASS |
| 09-09 | T1两独立spawn进程20轮，每轮两会话同参数只有一申请；T0变化409 | PASS |
| 09-10 | T1原进程结束→新进程20个action重放ID不变，另有WS断开REST立即重放；B实际服务重启后原申请保留 | PASS |
| 09-11 | 本地T1真实SQL提交后回包unknown只查回执；外部支付适配器不存在，支付超时子项按冻结范围N/A，不宣称支付故障通过 | PASS（本地）/N/A（支付） |
| 09-12 | T1管理员审核/审计持久；T0签收缺失不批准、审核不可覆盖；B审核意见与模拟未支付状态 | PASS |
| 10-01 | T1聊天两传输创建工单持久；B客户列表与进程重启后管理员同ID、owner202、原内容 | PASS |
| 10-02 | T0 ticket_update_tool不存在/非法状态迁移/事务失败；HTTP与工具均拒绝 | PASS |
| 10-03 | T1现存B工单A修改403；T0普通用户自有/他人都不能管理员更新 | PASS |
| 10-05 | T0 handoff_assignment/capacity非法/离线/忙碌/满载座席拒绝且记录不变 | PASS |
| 10-06 | T1 MySQL最后一个座席容量20轮、每轮两实际进程，仅1成功1冲突、有效指派计数1，审计20 | PASS |

当前没有通用Run表/资源接口，属于M2范围，AC-04-01的Run子项记N/A；当前证据由受管理员限制的Trace与会话历史承载，已构造B的真实Trace并拒绝A访问。不存在的接口404不作为“权限通过”证据。

## 失败保留与未验证范围

保留所有早期失败log：真实REST/WS完成事件前断线造成锁未释放；原回执重放误返回最新审核状态被最终校验拒绝；旧结构迁移测试夹具索引名错误（修正夹具，未放宽断言）；私有读取测试夹具缺orders.status（修正必填合成值）。最终以末次门禁为准，不用早期局部PASS覆盖后续改动。

未执行：真实业务库迁移/备份恢复演练、生产部署/代理日志审计、真实支付/通知、真实Milvus/embedding/reranker、全量真实模型质量/长时间负载、并发refresh轮换原子性、M2通用Run崩溃恢复。上述不在本次固定M1有效范围；真实支付明确N/A，不能把本次模拟审核用于到账承诺。Python/库的UTC弃用警告保留，不作为验收失败隐藏。

## 最终门禁记录

- 编译：`python -m compileall -q app scripts acceptance/m1`，退出码0。
- 最终T0：`python scripts/test_offline.py -q --tb=short tests`，**497通过、0失败、0跳过**，203.76秒，退出码0。证据`reports/offline/20261103-153256-188842/{manifest.json,junit.xml,pytest.log}`；代码已为2545ea9/232b233/b7b8ad6，后续f7bae0f只加验收入口和文档。
- T1：`python scripts/test_m1_real.py`，上一完整运行11通过，退出码0，`reports/m1-closeout/real-20261103-153055`。补强后单独复验：真实消息/Trace/反馈矩阵`real-20261103-153425`、stdio严格额外字段`real-20261103-153526`、旧退款记录迁移保留`real-20261103-153618`，各1通过、退出码0。最终已提交验收入口整套复验：**11通过、0失败**，退出码0，76.35秒，`reports/m1-closeout/real-20261103-153727/{manifest.json,junit.xml,pytest.log}`，提交f7bae0f。
- 前端：`npm test` **24通过/6文件**；`npm run build -- --outDir ../reports/m1-closeout/frontend-build`退出码0，tsc及Vite生产构建通过。未生成需提交的static产物。
- 模型：已授权4次，实际4次，每次max_output_tokens256，4/4通过；仅合成输入，未追加调用。
- B层：上述截图/AX与实际MySQL查询核对；浏览器末次console error为空；图、工具、SQL、最终validator均为实际实现，无模型调用。

实施/验收入口提交：`f7bae0fa052e1b7010b41ea3e95cd606198aee6f`；之后只封存文档。源文件/构建/合成数据指纹保存在忽略的`reports/m1-closeout/final-snapshot.json`，未保存密钥或密码：

```
source SHA256 236862b9c41f1f76ac18101d6ad630e3af3c200f1dddf74bea36d67a7b36296b
build  SHA256 8246689c572b25a2886d375b3eafd18ff03bba7bd4f69d808696e3fea997ed83
data   SHA256 03962c4e319b1bd042834bb9f6fb0e8be94c644996fbeaff60b6b77bfb38286d
```

合成数据核对：订单0.10 CNY、一个模拟批准未支付申请、一个取消动作、聊天创建工单owner202及对应审计；所有数据只在本次UI合成库。配置冻结：MySQL8/Redis7、独立端口54603/64260、action900秒；UI不配置LLM。未变更现有业务容器、数据库、知识库或数据卷。

本次本地提交（均不推送）：ca1b780冻结、0bd6fcd旧失败、d2ce4f4未知写结果、2545ea9退款、232b233身份隐私、b7b8ad6前端、f7bae0f真实验收入口；末次文档提交号以Git日志为准。

## 验收结论（已封存）

固定35项P0必做断言已逐项覆盖并通过本地验收；本阶段M1完成。AC-04-01通用Run与AC-09-11外部支付子项沿用冻结范围N/A，其现存资源/本地未知提交断言均已测试通过，不推广为生产/支付/全量模型验收。

封存时重新核对全部源码SHA256不变；检查21份T0/T1日志，没有裸JWT或本次实际测试数据库密码匹配。已脱敏模型摘要另存版本化M1-MODEL-EVIDENCE-v1.json，保留调用计数、结果、用量与原证据哈希，无密钥。

下一阶段建议按原方案进入M2（通用Run与执行恢复），需另行开工，不自动继续；当前退款仅模拟闭环。完整修改文件见M1-CHANGES-v1.md。
