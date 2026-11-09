# Judge v2固定方案验收收口

最新评测收口（2026-11-08）：24例标准已复核，新增oracle.v2修正4例泛问/条件政策的完成标签，旧文件与成绩保留。指定2次真实Judge当前发布包装政策语义复验通过（合成理解/订单上下文，非API整链）；宿主/断网容器相关各588通过。线上Judge v2保持启用、9服务健康；286/286余0。详见docs/MULTI-INTENT-ORACLE-V2-ACCEPTANCE-20261108.md与reports/multi-intent/oracle-v2-runtime/closeout.json。不得称24例全新模型完整复验或独立人工金标通过。

最新部署（2026-11-08）：用户授权“修复并部署”后，已修复部署Judge请求重复schema膨胀及20/40句边界不一致。API/Worker同镜像，九服务healthy，Judge v2和逐项开关已启用；宿主/容器相关各553通过，真实两项订单冒烟及WS greeting通过。当前284/286，余2保留。旧MI16完成标签存在争议，未据此宣称全量语义重新验收。见docs/JUDGE-V2-DEPLOYMENT-FIX-20261108.md和reports/multi-intent/deployment-payload-fix-r2/closeout.json；下文均保留为历史阶段。

最新状态（2026-11-08）：22组真实组件及3个整链通过；最后分号混合句已离线修复，宿主/断网容器各474通过，未启用/部署。当前178/187，余9含保留2；末图和部署固定10次尚缺3次授权。以docs/JUDGE-V2-ATOMIC-POLICY-RECHECK-v1.md及reports/multi-intent/clause-layout-revalidation/closeout.json为最新证据；下文阶段记录保留为历史。

最新复核（2026-11-08）：布局修订真实16次后总117/127；发现MI-06将无依据办理后果藏为general_advice误放，MI-16 answered+partial被正确拦截。已修订生成及Judge提示，真实复验尚未运行，仍未部署/启用。以[JUDGE-V2-ATOMIC-POLICY-RECHECK-v1.md](JUDGE-V2-ATOMIC-POLICY-RECHECK-v1.md)为当前记录，下文计数均保留为历史。

最新后续（2026-11-08）：用户授权修复并复验。已加入v2可选JSON对象协议，真实12个Judge回包语法合法；同时定位并修复脱敏器把服务端哈希数字误当手机号的冲突，使用无数字可逆ID。本阶段累计21/80、剩59；获准分段复验在MI-05事实/nonfact重复归类处停止，未启用。最终相关宿主/容器各425通过。详见JUDGE-V2-JSON-PROTOCOL-FIX-v1.md及safe-id-revalidation/closeout.json；下文3/80为历史。

**状态：未通过，未部署/启用。** 用户已明确新增授权80次HTTP，并要求固定采用v2完成验收后才启用。原多意图阶段122/122不动，其他旧阶段余9次不挪用。

## 固定范围与授权

准备脚本`scripts/prepare_item_v2_acceptance.py`冻结16条原回归及4条新编合成场景，组件理解/正确候选/错误候选共60次；MI-01/04/06/16四个完整图各最多4次，共16次；通过后部署合成冒烟最多4次，总80。完整HTTP JSON每次最多24576 UTF-8字节，输入总预留1966080字节，输出每次最多2048、总163840 tokens。输入预留不是供应商实际token或账单。Judge10秒/任务20秒不改。

所有任务失败或unknown停止后续任务，无SDK自动重试、JSON修补或失败操作重放。部署前须完成离线、真实语义、4图及单独生成文本复核，部署冒烟失败回滚；后台Worker不得产生未计账的付费调用。新增材料`acceptance/multi-intent/v2-fresh-cases-v1.json`为assistant编写合成标签，并非独立人工金标，也不承诺泛化准确率或生产容量。

最终冻结`reports/multi-intent/v2-acceptance-final-preparation-r3/`，prepared_id：`f3659b32128b67fca75b24c67567aab6d031caa7ab0fbb16195ad8f91d1728ef`。计划、授权、来源hash和18份保护账本hash一起保留。此前r1/r2仅评测准备失败，无真实HTTP，不替换已派发账本或抹除失败。实际执行器`scripts/run_item_v2_acceptance.py`复用统一业务评测链与模型网关，v2使用当前统一引用契约，保护历史账本；20例从头执行，不拼接旧成绩。

## 离线与执行器修复

最终宿主相关377通过，`reports/offline/20261108-000545-557166`；容器相关377通过，`reports/offline/20261107-160641-462694`。来源稳定，离线审计阻断0。此为相关回归，非最终全量。compileall、核心Ruff及diff检查通过。

完整SDK演练20组件、4完整图通过，74次合成HTTP、真实付费0。新增4例仅离线契约回放，真实阶段尚未执行，不能宣称新样例语义通过。

保留的准备失败：准备函数曾修改全局Judge配置影响3项兼容测试，移除副作用后重跑通过；Windows路径分隔符导致材料比对失败，统一仓库相对路径；第14例SDK替身将对抗候选引用当作Judge来源，修正替身以合法引用拒绝坏候选并加强合法回执断言；计划缺非敏感provider快照导致真实入口派发前拒绝；编排曾选到无完整字段的历史manifest，改为日志指向的准确报告。均未修改业务语义判决或门槛以过关，未消耗真实请求。

## 真实验收：首例非法JSON停止

新账户实际派发3次，均HTTP完成、usage已知，无超时/输出截断。

- MI-01理解完整覆盖3项诉求。
- 正确候选Judge function arguments再次输出`"answerability": full`，裸full是非法JSON。严格解析unknown，拒绝发布，不能计入正确候选通过。
- 错误候选Judge合法返回invalid，正确拒绝无条件承担运费的错误答案。

按组件失败立即停止其余任务：余19个组件（含全部4个新例）、4图、生成文本复核、部署冒烟均NOT_RUN。没有自动修补JSON、重放失败操作或打开v2业务开关。这是此前协议错误的再次出现，不能把“v2结构更合理”当作当前供应商能够稳定履行契约的证据。

原回执`reports/multi-intent/v2-final-acceptance/live/responses.json`，非法参数及hash另外保存在`invalid-provider-json.json`。当前源码和原始失败冻结不再改动。继续改业务/协议后必须重新绑定来源并明确续验方案，不能使用原计划偷跑。

## 账本、服务与结论

新增账户**3/80，剩77**，账本`reports/multi-intent/live-budget/f3659b32128b67fca75b24c67567aab6d031caa7ab0fbb16195ad8f91d1728ef/dispatch.sqlite`。原18份保护账本hash一致，包括原122/122及历史unknown；新阶段没有用量未知。独立新账户不覆盖旧阶段，剩77不表示失败后已获准自动重跑。

业务主栈未启动或改动：检查时app/mysql/redis/milvus/etcd/minio exited、worker restarting，embedding-local/reranker healthy，不能声称九服务健康。完整评测采用独立容器、临时SQLite和合成工具/知识；不证明真实业务MySQL/Redis/GPU或生产部署通过。临时评测容器及凭据文件已清理。

机器收口`reports/multi-intent/v2-final-acceptance/closeout.json`，状态`V2_FIXED_ACCEPTANCE_STOPPED_RECURRING_INVALID_PROVIDER_JSON_NOT_ENABLED`。下一步应先在受控小样本下验证供应商结构化输出兼容性/稳定性，再决定是否继续同一预算内验收。失败停止后的新派发须有明确续验指令；不得通过自动JSON修补、降低检查、变更标签或反复整批重试宣称通过。
