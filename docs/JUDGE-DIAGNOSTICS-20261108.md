# Judge诊断链路补齐（2026-11-08）

用户要求“进行下一步”，本轮实现并部署Judge故障诊断，未重启整批性能测试。

记录范围：输入指纹、原始回执全文SHA256和字节数、finish_reason、有界2048字符脱敏摘录、脱敏解析判决及允许列表失败码。完整原始用户/供应商文本不自动另存，认证头和密钥不进入诊断。非法JSON不修补，unknown继续拒绝发布、不缓存、不自动重试。不同请求通过返回对象绑定，未使用全局变量。

两处传递缺口分别发现并修复：发布前再次核验会丢诊断字段；Judge在独立异步任务内执行，父任务无法读到子任务ContextVar元数据。现在由本次解析对象携带回执，再随Judge结果进入最终状态。此前两次回答成功但诊断缺失的记录保留，不能追写为完整通过。

当前API/Worker同镜像2a08a526…（ai-customer:judge-diagnostics-v3-20261108），在上轮优化20f856b0…上仅修改llm_service、validation_llm、answer_review三个文件。配置/SQL迁移不变，九服务healthy。精确部署文件reports/judge-diagnostics-release-v3/private/candidate-exact.yml含凭据，勿输出或提交；回滚文件保留上轮优化版本。

验证：初始诊断镜像相关48通过，工作区66相关通过；异步回执补丁相关26通过，脱敏/非法回包专项6通过。最终镜像compileall通过，未完成最终镜像全仓回归，不将前版通过写成最终全量。

真实有界退款复验3次HTTP、共9次模型调用，usage全部已知；另一次启动未就绪连接失败为0供应商调用，原停止记录保留。最终复验验证诊断字段完整、供应商结束正常、判决解析成功且最终发布通过，账号禁用、退款申请0。证据reports/judge-diagnostics-smoke-v4/rows.json和reports/judge-diagnostics-release-v3/closeout.json；之前smoke-v1/2/3保留。

原refund/7偶发unknown没有复现，原供应商原始回执未保存，不能据此倒推它是预算、协议、证据绑定或供应商故障。现在有条件解释下一次异常；不把单条成功当稳定性或新P95。原性能STOP及51样本保留，未继续派发性能批次。


## 十条有界复验后续（2026-11-08）

当前部署镜像本身相关67通过。获准最多10条退款咨询，但首条即unknown，按首错停止，后9条NOT_RUN。实际4条供应商SQL调用均succeeded且usage已知，账号禁用、退款/投诉申请0。最终失败码judge_failed_or_invalid、输入hash已保留，但receipt=null；不能从账本成功倒推供应商JSON或绑定一定合法。原STOP和回执保留，未启动性能批次。

已离线复现失败分支的async ContextVar丢失：run_structured返回None时没有解析对象携带回执，父任务读不到子任务元数据。新修复让同一子任务返回(value,metadata)，失败码和脱敏回执均显式传递；unknown仍不发布、不重试。宿主/断网容器相关各50通过，非全量；修复暂未部署，业务仍诊断v3/2a08镜像九healthy。

证据reports/judge-refund-recheck-v1/closeout.json、STOP.json、rows.json；新补丁宿主reports/offline/20261108-201129-112064、容器reports/judge-failed-task-fix/bundle/reports/offline/20261108-121200-874235。原故障的精确根因仍未证明；下一次先验证失败路径诊断完整，再固定有界续验，不能将余9条视为自动重试许可。


## 已捕获的直接原因与协议修复（2026-11-08）

按用户要求继续诊断，部署失败子任务元数据显式返回补丁后，第二条真实退款咨询捕获合法JSON和正常stop回执。供应商将segment7“建议寄回前检查”及segment8“提供订单号进一步办理”同时列入claims与nonfact_segments，触发原互斥校验。不是本次缺证据、预算不足或超时；该次claim/nonfact重叠已由脱敏解析回执证明。此前无回执的unknown仍不能一概归为相同原因。

用户问题“退货需要保留原包装和配件吗？”合理清楚，政策咨询不需要订单号；本轮检索有三份直接相关发布片段，包括原包装损坏可替代纸箱、配件/赠品/发票、商品完好及电子产品条件，足以回答核心问题。候选附加电子产品条件、办理引导使句子增加是生成范围问题，但不构成用户问题错误。不能删除条件或绕过Judge来消除失败。

结构修复为非逐项v2可选LLM_JUDGE_SEGMENT_WIRE：每个服务器句子只有一个segments.sN分类，事实/非事实使用互斥结构；服务端绑定原句后还原原JudgeVerdictV2，再执行原覆盖、证据身份/版本/连续摘录和语义核验。旧双分类回执继续拒绝，不自动删冲突字段。逐项既有selector接口不变。默认开关false，实际新镜像显式true；prompt版本judge-exclusive.v1。

捕获在tests/fixtures/judge-captured-overlap-20261108.json（合成用户问题及公开政策，无凭据），sources在reports/judge-refund-recheck-v2/source-review.json。新接口专项7项通过，初始候选镜像56相关通过（新增启用路径专项在宿主另验，不当镜像全量）。当前候选543f4f13…，API/Worker同镜像，仍无SQL迁移。最多十条相同问题的有界真实复验记录于reports/judge-refund-exclusive-v1/，不能据此证明所有unknown已根治或P95通过。


## 最终修复收口与诊断码保留（2026-11-08）

单句互斥协议部署后，同一退款包装问题10/10条真实REST复验获准发布，共30次供应商调用且usage已知；合成账号全部禁用，退款申请、投诉工单和转人工均0。证据reports/judge-refund-exclusive-v1/closeout.json。它仅证明该固定问题的有界复验，不是全业务语义准入或稳定生产P95。

继续审计发现发布前assess_judgement再次评估unknown时会丢失严格绑定失败码。新增回归先得到1失败/19通过，再以固定reason及code允许列表传递诊断，不回显任意字符串。补充错误来源身份、版本、伪造摘录、无证据和unsupported反例；原始双分类回执仍被candidate_segments_not_exactly_reviewed拒绝。

最终补丁仅替换已部署基线的validation_llm.py，不部署整个脏工作区。镜像ai-customer:judge-contract-diagnostics-v1-20261108 / sha256:84729bcf5fe7acfa0d34d7741ebfda598228a16a7445850f22c0a425babc15cd，API/Worker一致且源码SHA256核对通过。部署前SQL无accepted/running任务；部署后九服务healthy，health/docs/openapi均200。断网容器65项相关测试及compileall通过，非全仓。当前补丁无新增付费调用；前述10条真实成绩属于互斥协议镜像，不追写为最终诊断补丁镜像真实调用。

当前结构问题属于Judge输出契约局部设计，真实资料支持原问题；没有证据证明整个业务架构错误，也不将所有历史unknown归为同一原因。私有candidate/rollback配置包含凭据，仅本地保留，不输出或提交。
