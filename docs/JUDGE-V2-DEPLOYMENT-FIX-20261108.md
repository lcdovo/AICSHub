# Judge v2 部署调用故障修复与上线（2026-11-08）

状态：本机已部署并启用。用户本轮明确要求“修复并部署”。当前结果取代此前“未启用/已回滚”的运行状态描述；历史失败、标签和账本均保留。

## 修复内容

实际部署知识检索返回的包装政策形成21个句子槽位。旧选择式schema为同一事项的每句话重复声明同一事实类型，实际来源快照复现的schema大小为20818字节，加完整问题、证据和协议字段后超过原24576字节派发上限。SQL记录的llm_judge为unknown/ValueError，不是供应商判定不支持；HTTP派发前被拦截，不能把这次失败归为模型不可用。

修复为每个事项复用一份事实schema，每个句子仍有独立的required槽位和本项来源枚举。相同来源快照的schema降至7227字节，完整请求通过原字节上限。没有删原文、裁条件/例外、扩大供应商调用预算或跳过独立审查。

另发现入口允许40句、内部聚合claims却只接受20条的不一致；实际21句回执可在发出后再次被拒绝。新增测试先复现，再将聚合条数与既有40句预算对齐。每句话的来源、支持状态、条件、事项归属及发布前绑定仍全部校验。一次句子内部的claims上限仍保留；这不是把空证据或错误事实放行。

修改位于app/agent/item_judge_selectors.py、judge_contracts.py、item_judge_v2.py，并提升回答策略版本为answer-pipeline.v14-shared-schema-forty-segments。新增tests/test_deployment_judge_payload.py使用实际发布知识快照，验证请求进入原预算、独立句子槽位和21句完整回执解码。

## 验证与部署

最终宿主相关553通过：reports/offline/20261108-030502-718404；最终断网容器相关553通过：reports/offline/20261107-190502-959666（容器UTC时间）。来源稳定、网络操作0。属于相关回归，不是最终全仓全量。compileall、修改范围Ruff F401/F821及diff检查通过。候选镜像pip check、Worker/MCP导入通过。

共享schema修复镜像先以合成账号做实际REST“订单状态＋订单金额”两事项验证，供应商理解和Judge各1次均成功，SQL ModelCallRecord均succeeded。任务completed，精确核对当前已签收与0.10元；相同client_message_id返回一致结果，原生WebSocket greeting事件含done/stream_end。合成用户已禁用，退款申请0。整个过程仍由原独立账本派发，最多2次，不自动重试。

最终数量边界修复镜像随后更新API/Worker，该次更新不包含供应商调用。最终镜像为ai-customer:v2-payload-fix-r2-20261108；API/Worker共用同一镜像、SQL库和Redis准入namespace，190份app/scripts源码与冻结来源一致，无临时挂载或受控entrypoint。九个服务全部healthy；Worker周期健康检查通过，/health、/docs、/openapi.json均200。schema仍为0013_agent_reliability，没有新增迁移。

业务配置已启用AGENT_ITEM_COMPLETION_ENABLED=true、LLM_JUDGE_CONTRACT_VERSION=v2、LLM_JUDGE_V2_OUTPUT_PROTOCOL=json_object；LLM_MAX_TOKENS=2048、LLM_MAX_RETRIES=0。退款确认、权限、幂等和发布前审查不变。所有私有配置备份仅保存在忽略的reports私有目录，不输出密钥。

## 额度与尚未声称的范围

当前新阶段累计284/286，余2为用户指定保留，未自动使用。旧122/122、旧111/120及历史unknown不动。新两次派发均http_complete，usage已知；其余30份账本hash保持。未发出的旧SQL unknown预留与真实HTTP派发次数分别记录，不能据此伪称供应商已经调用或未计费。

真实部署冒烟覆盖两项订单查询、SQL/Redis/Judge及WebSocket greeting；原包装知识查询的请求大小和完整回执形状使用实际发布正文做离线回归，修复后未额外进行真实供应商包装问答。最终边界修复镜像没有重复付费冒烟；此前共享schema修复镜像的成功实测及最终源码回归分别列明，不写成同一镜像一次完整真实验收。

MI-16、MI-15及衍生样例的泛问/条件政策完成标签存在争议，见reports/multi-intent/oracle-audit-20261108/findings.md。本轮没有改旧标签、没有用旧“四图completed”作为部署门槛，也没有宣称合成样例是独立人工金标或全部语义质量已重新验收。针对性故障修复与部署完成，不等于生产容量、长时稳定或全量语义泛化验收。

详细证据：reports/multi-intent/deployment-payload-fix-r2/closeout.json；真实冒烟与SQL证明在reports/multi-intent/deployment-payload-fix/。旧失败部署及回滚记录在extractive-item-revalidation/完整保留。
