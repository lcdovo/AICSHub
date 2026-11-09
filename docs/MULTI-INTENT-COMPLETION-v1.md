# 多意图逐项完成检查与语义评测

最新评测收口（2026-11-08）：24例标准已复核，新增oracle.v2修正4例泛问/条件政策的完成标签，旧文件与成绩保留。指定2次真实Judge当前发布包装政策语义复验通过（合成理解/订单上下文，非API整链）；宿主/断网容器相关各588通过。线上Judge v2保持启用、9服务健康；286/286余0。详见docs/MULTI-INTENT-ORACLE-V2-ACCEPTANCE-20261108.md与reports/multi-intent/oracle-v2-runtime/closeout.json。不得称24例全新模型完整复验或独立人工金标通过。

最新部署（2026-11-08）：用户授权“修复并部署”后，已修复部署Judge请求重复schema膨胀及20/40句边界不一致。API/Worker同镜像，九服务healthy，Judge v2和逐项开关已启用；宿主/容器相关各553通过，真实两项订单冒烟及WS greeting通过。当前284/286，余2保留。旧MI16完成标签存在争议，未据此宣称全量语义重新验收。见docs/JUDGE-V2-DEPLOYMENT-FIX-20261108.md和reports/multi-intent/deployment-payload-fix-r2/closeout.json；下文均保留为历史阶段。

最新状态（2026-11-08）：22组真实组件及3个整链通过；最后分号混合句已离线修复，宿主/断网容器各474通过，未启用/部署。当前178/187，余9含保留2；末图和部署固定10次尚缺3次授权。以docs/JUDGE-V2-ATOMIC-POLICY-RECHECK-v1.md及reports/multi-intent/clause-layout-revalidation/closeout.json为最新证据；下文阶段记录保留为历史。

最新（2026-11-08）：v2可选JSON对象协议与无数字可逆片段ID已修复，12个新Judge回包JSON语法合法；前5例历史分段覆盖，MI-05正确候选因事实/nonfact归类冲突unknown后停止。新80阶段21/80、剩59，旧122/122不动。最终相关宿主/容器各425通过；真实剩余15例、4图及部署未完成。见JUDGE-V2-JSON-PROTOCOL-FIX-v1.md及reports/multi-intent/safe-id-revalidation/closeout.json；以下为历史。

最新：用户授权独立新增80次固定v2验收，原122/122不动；最终相关宿主/容器377通过、SDK20例+4图74次合成请求通过。真实首例正确候选再次返回裸full非法JSON，3次后按失败停止，3/80、剩77，未部署/启用。新4合成场景及4个真实完整图未执行。详见JUDGE-V2-FINAL-ACCEPTANCE-v1.md；以下均为历史。

最新：逐项引用由claim片段推导，模型不再重复填写evidence_ids。最终宿主/容器相关各373通过；真实最后4次整链因非事实开场绑定边界失败，已离线修复但未经真实重测。总122/122、余0，未启用/部署。检查时主API及数据库等exited、Worker restarting，本轮未启停业务服务。详见JUDGE-V2-ITEM-INTEGRATION-v1.md文末与reports/multi-intent/item-v2-derived/closeout.json；以下计数按历史保留。

最新片段ID改造：已实现服务端完整证据片段绑定、宿主相关366通过；3组真实诊断中2组通过，MI-13逐项/claim引用不一致触发unknown后停止，整链未执行。累计118/122、剩4。未启用，本轮Docker不可用，现有服务健康未核验。详见JUDGE-V2-ITEM-INTEGRATION-v1.md文末及reports/multi-intent/item-v2-fragments/closeout.json；以下历史数字保留。

最新后续：Judge v2逐项接入已实现，相关离线通过；首组真实诊断因供应商非法JSON停止，未启用。累计106/122、剩16。以下v8及更早统计按历史保留；最新详见文末及JUDGE-V2-ITEM-INTEGRATION-v1.md。

再次复验最新状态：用户要求“验证”后新增6次，6次JSON均合法，但第3组坏候选证据摘录拼接非连续来源，严格核验unknown后停止；两组通过，3个正确候选接纳，剩余3组及整链NOT_RUN。累计112/122、剩10，最终宿主/容器相关各342通过，未启用。详见JUDGE-V2-ITEM-INTEGRATION-v1.md后续复验及reports/multi-intent/item-v2-verification/closeout.json。

日期：2026-11-07。**开发与离线修复已完成，真实语义准入仍未通过。** 最新状态为SEMANTIC_ADMISSION_FAILED_HOLDOUT_FALSE_REJECTION_NOT_ENABLED：获准分段复验覆盖15/16组件，14项正确候选接纳、0错误候选误放；MI-15留出正确候选被Judge误拒，MI-16及4个真实整链未运行。最终源码宿主/容器相关391项通过，不能冒称最终源码全量通过。分别授权的80/24/12/6次账户共使用104/122，剩18；原2次usage未知保留。功能未启用、业务镜像未切换。旧111/120及剩余9次不挪用。以下各轮结果按历史保留，最新证据见文末及reports/multi-intent/controlled-v8-closeout.json。

## 逐项需求和执行

ItemizedTaskUnderstanding对multi请求要求2至4个原话摘录，每项独立标明query_order/get_user_history/knowledge/troubleshoot/prepare_refund/unsupported。知识问题即使同一topic也保留不同项。程序分配稳定i编号，保存原话、来源消息ID与scope；继续任务保持原清单及顺序。原话重复、无来源、已登记订单/历史查询或退款申请从清单遗漏均拒绝。

TaskProgress持久化需求和最近已审回答的item_outcomes；TaskRuntime每轮创建item_observations/item_reviews。旧事实、知识、确认和审查不能从SQL进度跨轮复用。存量JSON缺少新字段仍兼容，无数据库迁移。

intent_items.py集中处理来源目录、缺项、审查契约与最终状态。订单/历史仍走原授权工具，单次读取可供多个独立字段问题引用，但回答分别检查。知识按每项自己的原话独立检索，退款政策为额外内部前置；整组开始前预留原工具预算，不提高4次工具/3次决策/20秒等现有默认限额。

检索在独立状态副本执行，避免后一个空结果清掉前一个成功结果；合并要求相同epoch及完整chunk/version身份，逐来源轮流选择，文本预算min(RAG_EVIDENCE_MAX_CHARS,4000)，超限整片跳过不截断。每项仅绑定实际留下的来源hash。缺订单、权限拒绝、资料缺失保留具体限制，其他独立项可以继续。上下文或epoch改变、父截止/租约丢失仍停止。

退款前提不满足、订单不可用或政策缺失不能准备预览；其他写能力标unsupported，不扩大工具白名单。预览仍是模拟待确认动作，须下一次本轮明确确认才能提交申请。确认回执只更新对应项submitted/cancelled，其他limited/waiting_feedback项不能因此整体完成。

## 逐项回答和审查

回答按【i1】等段落覆盖全部诉求。同一次独立Judge返回各项状态、原文摘录、限定证据ID、语义支持、必要条件/否定/例外保留及可回答性；程序核对编号无遗漏/重复、摘录确实位于对应段落、来源属于当前该项、操作阶段合法。资料完整却只补问属于不必要限制。缺少合法Judge回执是unknown，不能静默放行。

只有全部answered才completed；部分资料/能力缺失保持waiting_input，排障操作建议为waiting_feedback，退款预览为awaiting_confirmation。准备预览不能answered，排障建议不能等同解决；解决状态还需要有限明确的本轮用户报告及U1来源。生成修复仍最多一次并完整复审。最终出口核对判决绑定与知识发布版本后再核对各项并更新进度，变化时原判决失效。任务回答不进入普通回答缓存，回答策略升级到v5。

来源存在/身份匹配不证明语义蕴含。模型遗漏任意自然语言子意图、把故障误标成普通知识或合并两问，不能靠原文摘录校验完全发现。有限排障正常/解决句式也不保证覆盖所有表达。独立Judge仍可能与生成模型共享错误，因此必须实测误放、误拒及逐项覆盖。

任务详情API追加items，每项包含item_id/text/kind/source_message_id/status，原所有者/管理员权限保持。进度是最近已审回复的结果，不是下轮执行证据。新功能要求任务循环、SQL网关及独立Judge；最初只适配v1，后续已补齐v2逐句+逐项接入，不兼容配置在启动时拒绝，v2未自动启用。最新接入及真实非法JSON诊断见[JUDGE-V2-ITEM-INTEGRATION-v1.md](JUDGE-V2-ITEM-INTEGRATION-v1.md)。

## 语义材料和离线执行

acceptance/multi-intent/scenarios-v1.json冻结16个不同语义家族，开发8个/留出8个按家族拆分。覆盖多个知识问题、同订单字段、缺参/权限/无资料、条件退款、预览与提交混淆、否定、保修例外、工作日、排障待反馈和来源错配。每例有原话、合成当前事实、正确候选和错误/遗漏候选。

标签为assistant_curated_synthetic，**不是独立人工金标**。真实Judge输入只有用户消息、候选、当前合成来源及各项限定身份，不提供good/bad标签、标准答案或期望成绩。开发/留出分别报告，失败、超时和非法输出不能排除分母，停跑的未执行项明确列出。

evaluate_multi_intent.py --output <新目录> --prepare-live执行无网络程序契约回放并冻结方案。报告另外演示“Judge对错误语义说谎时，程序仍可能接受”的反例，明确不把手工回执通过宣传成模型误放率0。

run_multi_intent_live.py --prepared <计划> --output <新目录> --rehearse通过真实OpenAI/LangChain SDK、临时SQL模型预算、合成HTTP回执和完整生产图；网络审计外部调用0。替身成绩只证明协议/评分链路。首轮演练生成器错误模拟为普通文本而非LLMResponse，且过早关闭共享provider；修正评测替身与资源所有权后重跑，失败记录保留，未改业务断言绕过。

## 固定真实评测提案

要求新阶段明确授权，旧余9次不挪用。16次理解＋16次正确候选Judge＋16次错误候选Judge=48次；4个完整任务各最多6次=24次；总上限80，已登记操作最多72，余8次不自动扩展操作或重试。输入保守预留上限1,966,080 UTF-8字节，输出81,920 tokens，输入字节上界不是实际token或账单。

保持UINO deepseek-flash、thinking disabled及function_calling。非敏感provider配置、源码、材料hash和prepared_id冻结，派发前核对匹配授权。账本固定reports/multi-intent/live-budget/<prepared_id>/dispatch.sqlite，换报告目录不重置额度。失败派发计账，操作不可重做；任一组件用例失败停止后续组件/整链，任一整链失败停止其余整链。

授权必须来自用户本阶段明确答复，绑定prepared_id与全部上限；默认PROPOSED_NOT_AUTHORIZED，没有授权在读取凭据/派发前拒绝。完整16例为分母，门槛为全部逐项覆盖、正确候选接纳、合法Judge输出及0错误候选误放。整链合成工具/知识验证理解到发布，不证明真实MySQL/Redis/GPU、浏览器或并发。生成文本仍需另外复核，报告final_admission_allowed保持false，不自动打开业务开关。

## 验证记录

初始相关131项通过；扩大173项通过。新任务详情测试初版重复插入已存在的合成用户202发生UNIQUE失败，移除重复seed并挂原chat router后保持归属断言，33项通过。离线材料16/16契约回放通过；最终SDK演练16个理解、32个Judge及4个完整任务通过，共66次合成HTTP、真实付费0、阻断网络操作0，不能当模型质量成绩。

最终冻结材料SHA256：0d88270d3e4789f9beb638a11ae457d4ae1947665b38a750fc86b15a1bc4c98b。当前候选源码指纹ad13fbf2234f86c314ab82c557e8064a9381b0a18f6fdf0c3eb44bf85d5b1093。具体计划及SDK报告位于reports/multi-intent/final-preparation/与final-sdk-rehearsal/，机器prepared_id以proposed-live-plan.json为准。容器全量收口将在完成后追加，所有失败保留。

## 最终容器与真实评测收口

上述ad13fb指纹为首次完整评测源码。三个容器分片合计**2306通过，0失败/错误/跳过**：609项（reports/offline/20261107-065539-660994）、706项（20261107-064655-781077）、991项（20261107-064655-782319）。每片network=none、审计阻断操作0、源码起止完整稳定。已知50ms实验batch观察竞态用例所在分片串行执行，未修改其期限或断言。完整证据为reports/multi-intent/container-validation.json。

用户明确授权新增80次HTTP；首轮实际6次，完成MI-01/MI-02两个组件任务后停止。MI-01理解和正反候选均通过；MI-02正确候选被误拒，原因是Judge逐字摘录包含自身【i编号】，程序只在不含编号的段落正文中查找。坏候选保持被拒。原报表、派发、返回均保留，不能把首轮写成通过。

修正只将摘录匹配范围扩为**含自身编号的完整对应段落**，仍不能跨段、引用不存在正文或其他项来源；保留条件、状态、独立语义和知识核验。捕获回执及场景写入acceptance/multi-intent/captured-review-v1.json，注明原失败与来源hash。相关扩大回归200项通过，修正版SDK演练完整通过。用户随后明确批准沿用80次总额度完整复验，修订计划绑定原phase_prepared_id和原6行hash，使用revision-v2/前缀，未重置账本或提高上限。

修正版实际又派发6次，总计**12/80**。前两例理解2/2、正确候选审查2/2通过；MI-01错误候选拒绝，MI-02错误候选Judge超过原10秒截止，回执为unknown并拒绝发布。本次4次Judge中3次合法输出、1次超时，不能把超时等同正确识别错误，也不能声称16例全部通过或误拒率为0。按约定立即停止，**余下14例组件和4个整链任务NOT_RUN，独立留出未测**；无额外自动重试，不放宽期限以使评测通过。

唯一HTTP超时保留unknown_dispatched，保守计入已用请求及预留预算，不能推定供应商未处理或未计费。新阶段剩余68次不自动续跑；原6行逐列hash保持，另14份旧派发账本（含原111/120阶段）hash不变。

修正后最终容器定向**200通过**，network=none、审计阻断操作0、来源稳定；证据reports/multi-intent/fix-container-offline/20261107-070446-025181。**2306全量发生在摘录修正之前，不能称为最终修正版的一次全量通过。** 最终来源以revalidation-preparation-v2/proposed-live-plan.json及closeout.json为准；compileall、核心改动Ruff F401/F821、Compose配置与git diff --check通过。

机器收口：reports/multi-intent/closeout.json，状态IMPLEMENTED_OFFLINE_VERIFIED_REAL_SEMANTIC_GATE_NOT_PASSED。原始失败位于live-output/current，复验失败位于live-output/revision-v2；临时评测容器及provider.env已移除，账本保留。原业务镜像和九个healthy服务保持，没有业务迁移、重新索引、真实支付或外发通知。

## 用户授权后的受控续验 v3（2026-11-07）

用户明确要求“继续受控复验，通过后再启用”。沿用原80次总账本，原12条逐列hash及14份旧账本保持；原unknown请求保守计账、保持usage未知、原操作不可重放。采用独立revision-v3/操作前缀执行一轮新评测，不拼接先前成功结果：48次组件调用及4个整链各最多5次，共最多68次。未增加额度，未放宽原Judge 10秒、任务20秒等时限，无自动重试。准备文件见reports/multi-intent/controlled-preparation-v3/。

本轮只修改评测脚本和对应测试，app业务源码与v2一致；冻结来源b73bf4403857a52ce0e13b12e61ca53513ea7d83807ab2f29c397826497c5e98。最终容器一次全量**2311通过，零失败/错误/跳过**，源码稳定、network=none、阻断网络操作0，报告controlled-v3-container-offline-run2/20261107-071642-728233。宿主相关216项通过，SDK完整演练16组件+4整链通过、66次合成HTTP、付费0。compileall与宿主定向Ruff通过；容器无Ruff，不把缺工具当检查通过。

续验基础设施失败也保留：首个新增测试的空provider替身误触默认配置，改为明确的合成provider；首个容器测试命令漏传用例路径导致无测试收集；首个SDK演练因reports目录所有权错误失败。修正测试替身及编排命令/目录权限后，重新冻结并完成上述通过记录，均未触发真实HTTP或修改业务断言。

真实续验新增**3次**后按首个组件任务失败停止：MI-01理解覆盖3/3，正确候选Judge超过原10秒时限，返回unknown并拒绝发布；错误候选获得合法判决并拒绝。不能把超时当语义误拒结论或正确性通过。其余15个组件任务、全部4个整链及生成文本审查NOT_RUN。新增超时记录revision-v3/MI-01/good保持unknown_dispatched，与原revision-v2/MI-02/bad一起计账；累计15/80，13次http_complete、2次unknown_dispatched，剩余65次。已知用量输入31897/output5079 tokens，另2次未知，不能推算完整账单或p95。未发现可据此判定的供应商内部原因，也未为过关调整期限。

候选镜像仅构建为ai-customer:multi-intent-candidate-20261107（sha256:5c5e011842c6dc7a9d6e31b7f2dcc6de41a8cd8c4e92e07ed42296a94f0e9d10），pip check、断网逐项配置及Worker导入通过；没有切换业务镜像或打开开关。原API/Worker镜像831db200…及九个healthy服务保持。临时评测容器及凭据文件已清理。

当前收口状态OFFLINE_PASSED_LIVE_JUDGE_TIMEOUT_STOPPED_NOT_ENABLED，见reports/multi-intent/controlled-v3-closeout.json及closeout.json。上一轮closeout保存为controlled-v3-previous-closeout.json，原始派发和失败报表均保留。继续真实调用仍遵守失败停止规则，需要明确续验指令；剩余65次不足以登记原方案一轮最坏72次，不得自动新建额度或借用旧9次。启用条件未满足。

## 继续验收与语义失败收口 v4—v8（2026-11-07）

用户要求继续并完成验收，随后分别授权新增24、12、6次；每个新增额度使用独立SQLite账本，原80授权及创建标记不改。两条历史unknown_dispatched均保留计账。每次失败按组件任务停止，不自动重试、不降低Judge10秒或任务20秒期限、不改冻结材料标签。因完整重跑额度不足，用户最后明确批准最多42次的分段续验。

### 已落实的修复

- 程序已完成全部本轮观察且无剩余读/检索/预览动作时直接进入生成，省掉冗余finish模型调用；不据此完成任务，最终逐项Judge和发布证据检查继续执行。SDK完整链请求数从66降至62，这是合成演练次数，不能解释为已测生产p95改善。
- 逐项模式先输出1—4项清单，程序依据清单推导single/troubleshoot/after_sales/multi，供应商schema不再输出task_kind。单一问题仍保留原路径，两个以上问题使用逐项执行。相同topic/同一订单不同字段也应分项；自然语言是否完整拆解仍依赖模型，程序无法证明任意遗漏不存在。
- 修正条件售后与多诉求分类冲突、旧“只路由第一项”提示冲突。多项已登记的subrequests不能退成无清单single/after_sales。内部前置查单不等同用户独立查询诉求。
- 明确缺订单号时补问，禁止擅自读取聊天历史猜订单；权限和当前用户限制不变。
- 每个i项使用按类型/当前来源限制的动态Judge schema：未办理退款不能answered，缺信息的订单不能waiting_feedback，已有预览可awaiting_confirmation。answerability描述能否说明现阶段，不能与操作是否完成混为一谈；预览内容可完整回答，仍仅待确认。错误候选的limited/missing回执须可合法表达失败，不能用过窄schema把语义拒绝全变成解析失败。
- 真实HTTP新增不含密钥或请求头的阶段计时，失败/取消也保留已达阶段。账户组合有总次数硬上限、单操作去重、未知不退额度；更换输出目录不重置账户。

### 实际观察与验证边界

诊断调用单独计1次，3.76秒返回：派发前约1.20秒、TCP/TLS约66ms、等响应头约2.42秒、收包及解析约54ms。仅这次采样未复现超时，不能确定历史两次超时的供应商内部根因，也不能据此承诺p95。正式期限未改。

v4来源6560a6…全量三分片共2313项，初始2312通过/1个已知实验batch排队观察时序失败，原样单独复验0.15秒通过。该实验生产未启用；原失败、源码及断言保持。不能写成单次零失败全量。真实v4前7组件通过，第8暴露条件退款未分项和退款项状态误标，24次后停。

v5在MI-04发现理解擅加get_user_history及Judge把补订单号标waiting_feedback，12次后停。v6在MI-02发现同订单两字段被single清空items，6次后停。v7前6项通过，第7预览正确答案被新schema过度限制，21次后停。修复前均建立失败回放，原报告和捕获数据保留。相关捕获位于acceptance/multi-intent/captured-conditional-v1.json、captured-missing-order-v1.json、captured-preview-v1.json；未将原失败追写为通过。

最终v8来源**164cbabeadd873d9411912fc065a039bd16dbc17730dc70074d006890fd4f42c**，宿主相关391通过（reports/offline/20261107-190610-547539），容器相关391通过（controlled-v8-container-offline/20261107-110638-969199），network=none、阻断操作0、源码完整稳定；完整SDK演练62次合成HTTP通过、付费0。compileall和定向Ruff通过。v4全量在最终理解/schema变更之前，不能作为v8一次全量通过。

### 获准的分段真实复验：未通过

用户明确授权分段使用剩43次，新增最多42次。carried-evidence-v8.json将前6项与v7逐项比较：app源码仅validation_llm.py的预览schema分支/版本变化，其余app源码一致，前6项provider输入所依赖的提示/上下文逻辑及按例动态schema一致；保存每例schema hash。第7理解保持原真实输出，错误候选真实回执按修复后schema离线重新解析，仍拒绝；正确候选新调用。此为分段证据及捕获回放，**不是同一源码一次完整真实运行，也不是此前失败不存在**。

v8实际新增25次，全部HTTP完成，无本轮超时。分段累计15例理解覆盖15/15、正确候选14/15、错误候选误放0/15、合法Judge回执30/30；开发8/8正确接纳，留出已跑7例中6例正确接纳，留出第8例MI-16未跑。不得用已运行分母掩盖计划16例或把“合法输出”当语义正确。

MI-15正确候选原文“质量问题退货运费由商家承担。”保留了来源的必要条件；Judge却返回conditions_preserved=false，理由声称未说明质量问题前提，触发独立语义误拒。该项坏候选仍被拒，发布保护无绕过。系统按约定停止，MI-16和4个整链全部NOT_RUN，生成文本审查NOT_RUN。证据live-output/revision-v8/及holdout-failure-v8.json。没有修改留出标签、忽略条件判决、以程序硬编码纠正模型或靠反复重跑宣布通过。

累计分别为原80/80、新24/24、新12的0/12、新6的0/6，**104/122，剩18**。已知输入262616/output37904 tokens，另外2条usage未知保留，未核对供应商账单。原v8之前79条原账户逐列hash保持、14份旧阶段账本hash保持，旧111/120不动。剩18次不足以重新从头执行完整64次；任何失败后的续验仍需明确指令和可审查方案，不能自动重置或扩大额度。

最新closeout状态SEMANTIC_ADMISSION_FAILED_HOLDOUT_FALSE_REJECTION_NOT_ENABLED。候选镜像已构建未部署，原0013 API/Worker镜像831db200…及九个healthy服务保持，临时评测容器/凭据文件已清理。验收结论为**未通过，未启用**；剩余工作是独立Judge语义误拒改进与新证据、MI-16、4个完整图及生成文本复核，通过后才部署。冻结材料标签均为assistant合成，始终不是独立人工金标。

## v2逐项接入后续（2026-11-07）

用户授权开展v2接入。已实现同次独立调用的逐句claim/逐项状态双重检查、各项限定来源绑定、预览/用户本轮陈述来源和发布再核验；原v1保留，配置允许隔离v2候选但未切换业务。最终宿主/容器相关各340通过、SDK16合成HTTP完整演练通过。真实诊断使用原剩余18额度，仅首组MI-15派发2次：正确候选function JSON含裸full值，严格解析unknown并拒绝；坏候选合法拒绝。按失败停止，其余5组及完整图未执行，不证明v2更可靠。

最新累计106/122，剩16；原80/80和24/24账本保持，新12为2/12、新6为0/6；历史两条usage未知保留。最新closeout为V2_ITEM_BRIDGE_IMPLEMENTED_OFFLINE_VERIFIED_REAL_DIAGNOSTIC_FAILED_INVALID_PROVIDER_JSON；原v8收口在controlled-v8-closeout.json保持，旧原业务镜像及九服务healthy未动。完整证据和边界见JUDGE-V2-ITEM-INTEGRATION-v1.md及reports/multi-intent/item-v2/closeout.json。
