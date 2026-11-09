# 多意图语义评测标准v2与真实知识复验（2026-11-08）

本轮两项工作已在明确范围内完成：24例标准复核与版本化修正，以及2次独立真实Judge针对当前发布包装政策的语义审查。业务实现、配置、部署镜像未改，Judge v2与逐项检查保持启用，九服务healthy。

## 标准修正

新增acceptance/multi-intent/scenarios-reviewed-v2.json，oracle_version=multi-intent.oracle.v2，保留原24例来源和旧文件SHA256。逐条复核24例，20例保留；MI-15、MI-16、V2S-01、V2S-02共4例同家族问题修正：用户泛问运费，但来源只说明质量问题情形，正确回答应保留已知条件、说明其他情形未知，允许limited/partial和waiting_input，不强制全部completed。

评测器scripts/multi_intent_oracle.py分别记录protocol_valid、statement_supported、items_covered、task_stage_appropriate、all_items_completed。回答安全并正确可以验收成功，但不表示全部业务诉求完成。未知协议、超时、空引用或绑定失败仍不得通过，同时与语义不支持分别记录。政策咨询不必查用户实际审核状态，来源未提供的页面、办理效果、资格或退款结果仍严禁编造。

scripts/run_multi_intent_live.py仅在输入明确指定v2 oracle时启用新评分；旧路径和旧标签保持。新版本有独立expected_item_statuses/expected_task_statuses，不能拿新标准覆盖历史失败报告。原资料均为assistant编写或复核的合成标签，不是独立人工金标；旧holdout已反复用于开发，不能重新称独立留出。

24例新参考候选的契约离线回放全部通过。这不是24例重新取得真实模型成绩，也没有把旧MI16中的无依据页面建议改算通过。

## 真实知识复验

用户授权使用此前明确保留的2次HTTP。执行前从当前业务知识库检索包装政策，取得3个发布片段；发出前再次核对发布版本仍有效。使用当前业务相同的app源码、Judge v2、json_object协议、严格引用与逐项审查规则，输入为合成理解/订单上下文，SQL模型网关在隔离评测库中运行。没有向模型发送真实用户数据。

两次真实调用如下：

- KB-PRESENT：合成已知订单上下文与当前真实包装政策。协议、全文支持、条件和逐项状态均通过，task completed。
- KB-MISSING：缺订单号与同一份当前真实包装政策。政策项answered、订单项limited，task waiting_input；新评测正确将其判为成功而非要求completed。

两条正文均逐字保留全部当前来源，包括“如原包装损坏，请使用厚实纸箱”例外。单独文本复核通过。每例只调用一次独立Judge，无自动重试、无JSON修补，2条账本均http_complete，usage已知。请求大小分别19624/19857 UTF-8字节，低于原24576字节上限；输出上限2048 tokens不变。

范围必须准确：这两次是独立供应商Judge的语义审查，检索证据真实且为当前发布版本，理解及订单上下文为合成数据；不是两次完整REST/WS API整链，不含新的供应商理解/规划调用。旧部署的真实订单整链验证另外保留，不能拼成一次同源码全流程24例验收。

## 回归、预算与部署

宿主相关588通过：reports/offline/20261108-031812-005689；断网容器相关588通过：reports/offline/20261107-191812-244559（UTC）。源码稳定、阻断网络操作0；属于相关回归而非全仓全量。SDK演练2次合成HTTP、付费0通过。compileall、修改范围Ruff F401/F821及diff检查通过。

此前7条保留账户记录hash一致，其余30份账本hash一致。用户明确授权后使用最后2次，当前新阶段286/286、余0；旧122/122、旧111/120和历史unknown独立保留。不得再自动产生任何批量评测供应商调用。

本轮仅修改评测材料、评测脚本与测试，不修改app业务源码、业务.env或部署镜像。线上Judge v2/逐项检查继续开启，9个服务healthy。无需为评测工具更新重启业务服务。

本轮结论为：修正后的标准版本完成，指定两条真实知识Judge语义复验通过。尚未声称：24例v2标签全部新付费复验、独立人工金标泛化、全量真实API整链或生产容量验收。

证据：reports/multi-intent/oracle-v2-runtime/closeout.json、standards-review.json、live/report.json、live/results.json、text-review.json及preparation/。原失败、原标签、原账本均保留。
