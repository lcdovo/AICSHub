# Judge v2与多意图逐项完成接入

最新评测收口（2026-11-08）：24例标准已复核，新增oracle.v2修正4例泛问/条件政策的完成标签，旧文件与成绩保留。指定2次真实Judge当前发布包装政策语义复验通过（合成理解/订单上下文，非API整链）；宿主/断网容器相关各588通过。线上Judge v2保持启用、9服务健康；286/286余0。详见docs/MULTI-INTENT-ORACLE-V2-ACCEPTANCE-20261108.md与reports/multi-intent/oracle-v2-runtime/closeout.json。不得称24例全新模型完整复验或独立人工金标通过。

最新部署（2026-11-08）：用户授权“修复并部署”后，已修复部署Judge请求重复schema膨胀及20/40句边界不一致。API/Worker同镜像，九服务healthy，Judge v2和逐项开关已启用；宿主/容器相关各553通过，真实两项订单冒烟及WS greeting通过。当前284/286，余2保留。旧MI16完成标签存在争议，未据此宣称全量语义重新验收。见docs/JUDGE-V2-DEPLOYMENT-FIX-20261108.md和reports/multi-intent/deployment-payload-fix-r2/closeout.json；下文均保留为历史阶段。

最新状态（2026-11-08）：22组真实组件及3个整链通过；最后分号混合句已离线修复，宿主/断网容器各474通过，未启用/部署。当前178/187，余9含保留2；末图和部署固定10次尚缺3次授权。以docs/JUDGE-V2-ATOMIC-POLICY-RECHECK-v1.md及reports/multi-intent/clause-layout-revalidation/closeout.json为最新证据；下文阶段记录保留为历史。

最新后续：v2显式json_object协议和脱敏安全片段ID已修复并有实测；本阶段21/80、剩59，分段MI-05正确候选因同句同时归为事实和非事实被拒，完整验收未通过、未部署。宿主/容器相关425通过，见JUDGE-V2-JSON-PROTOCOL-FIX-v1.md。以下各轮数字按历史保留。

最新独立80次授权阶段：固定v2从头验收，宿主/容器相关377通过、SDK20例+4图74次合成调用通过；真实首例正确候选再次出现裸full非法JSON，3次后停止，新增账户3/80、剩77，旧122/122不动，未启用。详见JUDGE-V2-FINAL-ACCEPTANCE-v1.md及reports/multi-intent/v2-final-acceptance/closeout.json。以下历史计数保留。

最新：已统一论断/事项引用，供应商不再输出逐项evidence_ids；最终宿主/断网容器相关各373通过。最后4次真实整链因事项前非事实开场句的绑定边界失败，已离线修复但无额度重测；累计122/122、余0，未部署/启用。服务现状见文末，不沿用历史九healthy结论。

最新状态：已改为服务端完整证据片段ID，宿主相关366项通过；真实3组中2组通过，第3组因逐项来源与claim来源不一致停止，累计118/122、剩4。未部署启用，本轮Docker不可用，服务健康未核验。详细记录见文末；此前数字按历史保留。

2026-11-07。**接入实现和相关离线验证完成，真实诊断未通过，未部署/启用。** 本次没有改变底层模型，仍使用UINO deepseek-flash、thinking disabled、function_calling。

## 接入行为

`app/agent/item_judge_v2.py`将现有v2逐句论断审查和逐项完成契约合并在**同一次独立Judge调用**中。v2继续核对所有句子的claim/nonfact覆盖、来源ID/版本、逐字摘录及支持判断；item_checks继续核对各诉求的原话、段落、条件、可回答性和实际完成阶段。两个层面均通过后才能发布，不能用总分或一项成功替代其余诉求。

每个claim必须位于相应i段落且只能引用该项允许的本轮来源。对于answered、waiting_feedback、awaiting_confirmation的通过判决，要求对应段落存在有支持的claim，item_checks所列来源也必须在该项claims中实际审查；不能把事实句放进social/advice或借其他项来源冒称完成。语义蕴含、claim是否完整仍依赖模型，程序不能证明任意自然语言判断正确。

订单/历史工具身份绑定当前真实工具回执；知识保留source_id/version_id；退款预览与本轮用户报告追加为独立、明确标注的审查来源。预览只说明awaiting_confirmation、application_submitted=false，不表示提交或支付；用户报告只说明用户本轮陈述，不表示系统独立验证设备正常。追加来源不执行新业务工具、不授予权限、不复用历史确认。

发布前同时重核回答/问题/知识/原工具输入指纹和item_context指纹。归一化item_checks必须与原始v2回执一致，不能在组件之间被改写。原始证明只在组件调用之间临时携带，不写入常规审查元数据；元数据保留hash、来源身份、状态及错误码。最终出口仍执行原知识发布快照、引用、金额、权限及逐项状态检查。任务回答仍不进入普通回答缓存。

配置现在允许任务循环+SQL网关+独立Judge下组合`AGENT_ITEM_COMPLETION_ENABLED=true`与`LLM_JUDGE_CONTRACT_VERSION=v2`。默认值及业务.env均未调整；没有启用Judge/任务循环/网关时仍拒绝不兼容配置。保留v1逐项和普通非逐项v2路径，无SQL迁移。

## 离线证据

新增`tests/test_item_judge_v2.py`覆盖16个固定合成场景的契约回放、跨项来源/版本/摘录攻击、漏句/漏项、把事实藏成非事实、预览冒充完成、本轮输入改变、归一化元数据篡改、完整审查到发布和配置保护。这些回执为显式替身，**不证明真实语义质量**。

最终宿主相关**340通过**：`reports/offline/20261107-192502-045571`。断网容器同范围**340通过**：`reports/multi-intent/item-v2/final-container-offline`，源码完整稳定、网络审计阻断0。compileall、核心范围Ruff F401/F821、git diff --check通过。此为相关回归，未运行最终源码全量。

完整SDK演练覆盖6组正反候选+1个完整图，**16次合成HTTP、付费0**，容器network=none。初始新增发布测试因替身缺少`_add_rag_citations`失败，修正替身后通过；容器编排初次因目标目录缺失及reports权限失败，保留日志，修正编排/所有权后重新验证，没有改变业务断言或触发真实HTTP。

## 有界真实诊断：首组非法JSON停止

沿用已授权且未使用的12次和6次账户，单独维持各自上限。原80/80、新24/24及所有旧账本不改。新固定方案最多18次：MI-15/MI-04/MI-07/MI-08/MI-11/MI-13共6组正反候选12次，全部通过后MI-01整链最多6次；没有新理解组件评测、没有全16例准入或v1/v2同输入新鲜对照的承诺。执行器`scripts/evaluate_item_judge_v2.py`在凭据初始化前核对计划/授权/来源/材料，守卫传输只允许固定供应商/模型/路径、先持久记账再派发，关闭重试及流式。每个组件任务失败停止。

实际仅派发首组MI-15的2次请求，均正常返回且usage已知，没有超时/截断：

- 正确候选：供应商function arguments写出`"answerability": full`，裸`full`不是合法JSON，生产SDK解析失败，返回unknown并拒绝发布；不修补、不重试。虽然可读字段中的逐项条件标记为true，**不能把非法回执记为语义通过**。
- 错误候选：合法v2回执，正确拒绝“电源已经正常/故障已排除”的无依据结论。

随后停止。其余5组和完整图NOT_RUN，生成文本审查NOT_RUN，不能估计泛化误拒率或证明v2比v1可靠。只有历史v1回执作参考，没有新的同输入双模型对照。MI-15原留出失败已用于开发诊断，不再当作独立未见留出。

非法JSON原文保存在`acceptance/multi-intent/captured-item-v2-invalid-json-v1.json`，注明来源报告hash和原失败，新增测试确认严格拒绝。真实阶段源码879b6b6…；实测后只增加捕获材料和回归测试，app未再修改，最终测试来源见closeout的final_source_fingerprint。不把事后捕获测试宣称为实测前已通过。

本轮新增2次，已知输入8886、输出1136 tokens，usage未知0；累计**106/122**：原80/80、新24/24、新12为2/12、新6为0/6，**剩16**；历史2次usage未知仍保留计账。原80和24账本以及所有protected-ledgers哈希一致，没有挪用旧111/120阶段的9次。失败停止后再次派发需要明确续验指令和固定方案，不能自动续跑或重置账户。

## 当前结论

v2逐项接入已完成，可在隔离验证环境选择；真实协议诊断未通过，不能据此切换业务开关。旧0013 API/Worker镜像831db200…及九个healthy服务保持；临时评测容器与凭据文件已清理，没有真实支付或通知。

机器收口：`reports/multi-intent/item-v2/closeout.json`；原始结果`item-v2/live/`、SDK结果`item-v2/rehearsal/`、冻结计划`item-v2/preparation/`。现阶段应先验证供应商结构化输出的稳定性，再评估语义和完整链路；不通过自动JSON修补、弱化原文绑定或增加隐式重试掩盖问题。

## 用户要求“验证”后的独立复验（2026-11-07）

本轮不修改app代码/提示或底层模型配置，只扩展评测器以使用独立`item-v2-verify/`操作前缀，并绑定前2条派发的逐列hash。保持12次账户已用2次、6次账户未用的原授权，不新建/扩大额度。固定方案6组正反候选12次，全部通过后整链最多4次，共最多16次；失败停止，无自动重试/JSON修补。

复验前宿主和容器相关各341通过，SDK6组+整链16次合成请求通过。真实复验实际6次，原MI-15和缺订单号MI-04两组均通过，退款预览MI-07正确候选通过。**6次返回均合法JSON**，未复现此前裸full问题，但样本很小、没有协议修复，不能宣称结构化输出已稳定。

第3组的坏候选被模型判断invalid，但回执引用把来源JSON中的`application_submitted`和`stage`两字段拼在一起，跳过中间的`order_id`。引用不再是连续原文，严格核验返回`evidence_quote_not_in_current_source`，回执unknown，发布仍拒绝。该调用不能计作合法语义拒绝或整组通过；没有因为模型的最终判断碰巧正确而忽略非法引用。按约定停止，余3组及完整图NOT_RUN。

已将原始失败保存为`acceptance/multi-intent/captured-item-v2-quote-gap-v1.json`并增加回归，业务代码未改。最终宿主/断网容器相关各**342通过**，源码稳定、网络审计阻断0；最终不是全量回归。宿主证据`reports/offline/20261107-193215-486933`，容器`reports/multi-intent/item-v2-verification/final-container-offline`。真实结果`item-v2-verification/live/`，最终机器状态`V2_VERIFICATION_STOPPED_EVIDENCE_QUOTE_BINDING_FAILURE_NOT_ADMITTED`，见该目录closeout.json。

本轮新增6次，已知输入26378、输出3346 tokens，新增usage未知0；累计**112/122，剩10**，账户分别80/80、24/24、8/12、0/6，历史两次未知usage保持。原2条v2记录、80/24账户及保护账本hash保持；没有借用旧111/120阶段余量。临时评测容器/凭据清理，原0013 API/Worker镜像和九healthy服务保持，v2未部署启用。

当前只能确认本轮已执行的3个正确候选被接纳、2个坏候选取得合法拒绝、1个坏候选因证据摘录绑定不合法为unknown；没有足够证据证明v2整体更可靠、结构化输出稳定或完整验收通过。继续验证需要先处理证据摘录表达的稳定性，并保持逐字绑定约束；失败停止后再次真实派发需明确续验指令和有界方案。

## 服务端证据片段ID及剩余额度验证（2026-11-07）

用户要求执行下一步1/2/3。逐项v2供应商契约改为`server-fragments.v1`：每个claim的evidence仅输出`fragment_id`，不再抄写quote/source_id/version_id。服务端为每个完整来源分配ID，hash包含本轮上下文、来源身份、版本和原文全文；目录列出对应短ID和允许的事项。使用完整来源，不按字段截断或重组JSON；模型选择ID后仍须判断语义支持及适用条件，引用存在本身不构成支持。

接收端只接受当前目录ID，恢复完整原文及身份后继续运行原v2论断核验，再执行跨事项来源和完成阶段检查。缺失、伪造、跨轮、旧版本、跨项、重复引用或夹带旧quote字段均不能放行。逐项回执所列来源仍须在对应claim中实际被审查，不能因改用ID而弱化这项约束。普通非逐项v2保持原契约；旧摘录回执可用于离线历史回放，生产逐项调用使用新的严格wire schema，不自动将旧回执转换成成功。

新增16场景片段回放，以及伪造/跨轮/旧版本/跨项/重复/旧quote/逐项未审来源回归。最终宿主相关**366通过**，`reports/offline/20261107-233148-677198`；compileall、核心Ruff和diff检查通过。非全量回归。早期一轮运行中源码变化被证据检查拒绝，已在源码冻结后重新通过，原报告保留。

SDK合成演练3组正反候选+1整链共**10次**通过，真实付费0。首次演练的替身沿用夹具轮次ID，被跨轮保护拒绝；改为选择当前图请求实际提供的片段ID后通过，未放宽业务规则。Docker引擎管道不可用、未发现标准安装位置的Docker Desktop，故本轮使用宿主临时SQLite隔离执行，未运行容器回归或核验现行业务服务健康，不沿用历史nine healthy当作本轮证明。没有部署或修改业务.env。

真实固定方案为MI-07退款预览、MI-08条件不满足、MI-13排障三组正反候选6次，全部通过后MI-01整链最多4次，最多10次。独立`item-v2-fragments/`前缀，绑定原8条v2派发hash及既有账户原授权；不新建额度，失败按任务停止。

实际新增6次：MI-07/MI-08两组正反候选均通过，原摘录拼接故障在执行的片段调用中未出现；MI-13正确候选的item_checks列出了claim未引用的来源，触发`item_completion_reference_not_reviewed`，unknown并拒绝发布。该组坏候选合法拒绝。累计3组中2组通过、正确候选2/3、错误候选误放0/3、合法绑定回执5/6。**没有运行真实整链**，不能将本次改造或两组通过当作完整验收。

原始MI-13回执保存在`acceptance/multi-intent/captured-fragment-crosscheck-v1.json`，新增同模式回归确认逐项与论断来源必须一致。实测后仅增加捕获/测试，未改app语义判断。本轮真实输入26228、输出3361 tokens，新增usage未知0；总计**118/122、剩4**，账户80/80、24/24、12/12、2/6，历史2次usage未知不退额度。原8条v2及保护账本保持，旧111/120阶段未用。

机器收口`reports/multi-intent/item-v2-fragments/closeout.json`，状态`SERVER_FRAGMENT_REFERENCE_IMPLEMENTED_DIAGNOSTIC_STOPPED_ITEM_CLAIM_REFERENCE_MISMATCH`。片段ID实现和回归完成，真实验证未通过，未启用。Docker不可用时未创建临时评测容器，宿主直接从配置读取凭据且未生成临时凭据文件；输出不含密钥。失败停止后继续派发仍需明确指令和固定方案，剩4次不能重新完整复验。

## 单一引用来源与最后4次整链检查（2026-11-07）

用户明确要求执行下一步。逐项v2 wire契约升级为`server-fragments.v2`：`item_checks`不再包含evidence_ids，额外填入该字段会被严格schema拒绝。模型只在claims选择fragment_id；服务器验证本轮片段身份、版本/全文和claim所属i段落后，推导每项实际引用来源。独立的status、supported、conditions_preserved、answerability仍由Judge输出并核对，不因为来源可推导就默认诉求完成。

同一claim不可跨项借源，事项完成仍要求有支持的事实论断。漏论断、伪造/旧版/跨轮ID、重复引用、预览冒充完成、遗漏事项继续拒绝；不再让模型重复选择同一套来源后相互冲突。普通非逐项v2及离线旧回执回放保持兼容，生产逐项使用新wire契约，供应商旧字段不会被默默转换。

首次相关371通过；补齐固定四次预算保护后372通过。SDK完整图4次合成HTTP通过。真实限定为MI-01订单+两个知识问题整链最多4次，不新增组件调用，使用原最后4次余额，不重复旧操作、不重置账户；阶段包括理解、一次执行决策、生成、独立v2审查。该范围始终只是整链冒烟，不是完整16场景语义验收。

真实4次均完成，但最终发布拒绝、任务stopped：生成候选在【i1】之前添加“您好，关于您的问题逐项回复如下：”，v2将此句列为nonfact social；桥接原实现却要求每个segment（包含非事实）都在某个i段落内，抛出item_segment_outside_section。最后发布保护没有取得可用逐项审查，保留失败、未发布草稿。没有以模型整体valid判决覆盖程序绑定失败。

离线新增失败回归先复现该边界，再修正为：v2已经严格核对全部句子的claim/nonfact覆盖，只有不属于任何事实claim的非事实开场可留在事项段落之外；它不提供来源、不完成事项，所有事实claim仍必须有明确事项归属。模型将事实误分类成非事实的风险仍由既有v2语义审查与确定性底线共同约束，不能把此修改宣传成消除全部语义错误。

最终修复版宿主/断网容器相关各**373通过**：宿主`reports/offline/20261107-234409-694221`，容器`reports/offline/20261107-154543-964428`，完整稳定来源、blocked_network_operations=0；compileall及定向Ruff通过。修复后SDK完整图4次合成HTTP再次通过。原真实失败源码104d7bbd…与修复后源码分别记录，**修复后没有真实模型验证，不能把离线演练当作真实通过**。捕获`acceptance/multi-intent/captured-derived-e2e-v1.json`与red日志、所有历史报表保持。

本轮新4次，已知输入11864/输出1404 tokens，usage未知0。累计**122/122、余额0**：80/80、24/24、12/12、6/6；历史2次usage未知不退额度。此前14条v2派发逐列hash与保护账本保持，旧111/120阶段未用。后续任何付费验证须新授权和固定额度，不能挪旧9次或通过换目录重置。

Docker本轮起初不可用，后续恢复，已在源码只读挂载、network=none的临时容器补跑373项并自动删除。检查时API、MySQL、Redis、Milvus、etcd、MinIO均exited，Worker restarting，Embedding/Reranker healthy；本轮没有执行主栈启停、迁移或部署，不能沿用历史“九healthy”。业务配置开关未改，v2未启用。

最新机器收口`reports/multi-intent/item-v2-derived/closeout.json`：`DERIVED_ITEM_SOURCES_IMPLEMENTED_REAL_SMOKE_FAILED_PREAMBLE_OFFLINE_FIXED_NOT_ADMITTED`。引用统一和离线修复完成，正式验收仍未完成，需新的有界真实修复验证及剩余语义/整链审查，不能自动部署。
