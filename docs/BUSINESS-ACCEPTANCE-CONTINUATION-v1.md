# 评测链盘查修复与受控续验v1（2026-11-08）

> 后续退款读计划v2已执行：当前19/168，新增3次后在BA-05检索后建议人工处停止。提示冲突已修复，正向退款评测证据缺口已补离线预检和独立候选；未部署、未完成退款两轮验收。本文16/168保留为历史，最新见BUSINESS-REFUND-SCOPE-REVALIDATION-v2.md及refund-scope-v2/closeout.json。

## 最新结果

用户指令“盘查修复尽量不要出现这种错误了，然后继续”已记录，按同一168账户、最多新增148次的固定提案续验。原9条记录逐列保护，原33份外部派发账本hash保持，无额度重置。

本次新增真实HTTP7次，累计16/168、余152；16条供应商usage均已知。本次148续验上限已用7，余141操作slot因首错闸门停止，不自动使用。原失败STOP与新continuation-v1/STOP都保留。

BA-04两轮真实WS排障已通过单独assistant文本复核：第一轮依据本轮资料指导开机/蓝牙/删除旧配对，等待反馈；第二轮提供新的仓内按钮10秒重置动作，未重复上轮核心步骤、未声称解决。两次真实SSE均取得usage并完整结算。该场景精确回放先前已成功的2条理解/决策响应，不重新计费；新真实调用6次。成绩必须注明分段等价输入复验，不是全新一次完整供应商运行，也不外推生产容量。

继续BA-05/t1时，供应商返回合法JSON/Schema，但“单项无条件退款申请”同时填入read_tasks=query_order，触发原有read_plan_requires_query语义校验。评测器捕获原校验失败，未将安全回退或HTTP200误算通过；按首错停止，未发起第二轮、BA-06至BA-20及最终6例。没有生成退款预览、登记申请或真实支付。当前累计4例场景通过，不是20例语义准入完成。

## 已修复的评测问题

- 新scripts/business_http_boundary.py支持JSON与native SSE：派发前持久预留，严格检查endpoint/model/请求字节/输出上限/重复操作；流式须明确授权并请求include_usage。DONE和stop结束后才完成正常结算，缺usage/截断/取消保守占额、超时不重试；length保留已知usage但拒绝业务通过。
- 响应HTTP解压与SSE解析分离，避免gzip响应被误判成截断；不修改模型正文、不修补JSON。SDK回归先复现gzip失败后通过。
- 评测器主动记录原理解语义校验失败，即使任务仍为waiting_input也不能把降级答复当等待反馈成功。保留权限、条件、来源及本轮确认校验，未删除/跳过read_plan_requires_query。
- 每次真实启动强制核对宿主/目标镜像测试manifest、源码快照、四格SDK预检和代码hash；证据不匹配在读取Key/派发前失败。原业务源码与案例冻结，新增文件仅限三个评测模块和两个回归文件。
- 补齐原始单项/多项×REST/WS四格矩阵；单项覆盖两轮反馈。预检捕获的替身语义非法、额外字段、Windows269字符路径及并行包构建竞争均保留失败记录，修复后再演练，付费0。
- 构建包原子发布、防止并行看到半成品；只复制指纹清单内文件，拒绝.env、符号链接和包根app shadow。凭据用短路径私有临时env，finally删除；Docker超时只清理匹配nonce标签的本任务临时容器；运行锁防并行真实执行。
- 重复请求/已完成场景slot退休、授权撤销、未复核前一场景、旧9行/外部账本/业务源码/场景/运行镜像漂移均失败关闭。同一账本、每轮6次、总168次与本次148新增上限保持。

## 证据与验证范围

原Bug反馈命令：python scripts/test_offline.py -q tests/test_business_http_boundary.py --tb=short；保留先红1失败/1通过报告reports/offline/20261108-145254-626327。最终宿主相关93通过（reports/offline/20261108-152346-879639），目标镜像断网相关90通过（reports/business-acceptance-v1/c1-container-tests-release/）。均源码稳定、网络阻断0，属于相关切片非全仓。

宿主Python3.13仍有SDK/HTTPX异步生成器结束警告及原datetime警告；未通过改业务逻辑或屏蔽警告让测试变绿。实际续验固定在目标镜像Python3.11，目标镜像本轮90项无上述警告。不声称所有平台生命周期/长期稳定性通过。

四格SDK矩阵及单项两轮预检在原镜像断网执行，金融账本只在明确标识的Mock副本中演练，实际账户始终9直到正式续验。最终执行绑定见live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/continuation-v1/execution-binding.json。代码和案例hash匹配后才允许真正调用，所有Mock响应不算业务语义成绩。

当前真实ledger已知输入tokens 42202、输出tokens 4071；输入预留158135 UTF-8字节、输出预留18104 tokens。价格未核验，不当实际账单。原BA-04失败隔离SQL中的1条generation_stream保守unknown预留原样保留，与当前16条真实供应商usage已知分开记录。

## 新退款理解问题的离线候选

捕获显示apply、conditions=[]、单项prepare_refund并携read_tasks。冻结提示同时包含“内部前置读登记read_tasks”和通用“无条件申请read_tasks为空”，存在客观指令冲突；一次回包不能证明这是唯一原因，也不能证明修订必然提升模型质量。

已准备仅澄清ITEM_UNDERSTANDING_PROMPT的补丁：单项无条件申请read_tasks必须[]，订单读取仍由程序安排；条件办理与明确多项查单保留合法读计划；不得改申请为query、虚构条件或增加用户没有提出的查单事项来规避约束。未改校验器、未自动删坏回执字段、未修补JSON。

候选位于continuation-v1/read-plan-prompt-candidate/，原坏回执与candidate.patch保留；6项断网契约测试通过：原坏输出继续被拒绝、单独构造的正确输出仍先读订单、条件/显式多项读取仍合法。它只是提示文本和程序安全契约验证，不是供应商语义通过；未应用到工作区app源码、未部署、未产生后续真实调用。若采用新提示，原4例只作历史，不能直接算新提示的最终质量成绩。

## 当前运行状态

业务API/Worker继续使用d8b78495…/batch20，业务配置、SQL迁移和发布知识不变。九服务健康；评测SQL/订单/用户均隔离合成，临时凭据和评测容器已清理。严禁因剩152或本次余141而自动重跑失败操作；下一真实复验须先明确新提示/协议版本和固定方案，保存原计费及失败。

最新机器证据：reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/continuation-v1/closeout.json、cases/BA-04和BA-05、read-plan-prompt-candidate/evidence.json。准备阶段提案文件保留未确认原稿，实际续验授权以continuation-v1/authorization.json为准。
