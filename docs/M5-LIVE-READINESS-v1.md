> 2026-11-05后续状态：用户已明确授权，本计划1027/1027次真实请求已完成，质量门槛FAIL，无剩余额度。完整结果与离线修复见M5-LIVE-EVIDENCE-v1.md；以下保留当时准备记录，不能沿用其“未授权/未调用”表述作为当前状态。

# M5真实生成与Judge评测准备

2026-11-05。本轮只完成执行器、额度方案和离线演练，没有新的供应商请求。原M4真实额度保持660/660，M4/M5整体门禁和性能暂缓状态不变。

## 本轮可审阅方案

固定计划为`docs/M5-LIVE-PLAN-v1.json`，状态PROPOSED_NOT_AUTHORIZED；`scripts/evaluate_m5_live.py --prepare`只生成计划，不创建付费授权或派发请求。当前主模型配置为UINO deepseek-flash、关闭思考、function_calling，实际推理/网络可用性尚未新验。

| 顺序 | 固定材料 | 生成上限 | Judge上限 | HTTP请求上限 |
| --- | --- | ---: | ---: | ---: |
| 1 | 无答案/部分答案/业务边界60题 | 60 | 60 | 120 |
| 2 | 固定审查反例100题 | 0 | 100 | 100 |
| 3 | 固定正确候选105题 | 0 | 105 | 105 |
| 4 | 可回答351题 | 351 | 351 | 702 |
| 合计 | 616题 | 411 | 616 | 1027 |

单次生成最多512输出tokens，Judge最多256；累计输出预留上限368128。累计输入预留上限16000000，使用**完整HTTP JSON的UTF-8字节数作为保守token上界**，不是预估实际usage。每个请求JSON最多24576字节，超限在派发前拒绝；不能通过截断证据挤入预算。失败/未知请求仍计入额度，单进程顺序执行，不重生成、不自动重试，也不据失败新增预算。

公开/本地费率未完成账户倍率与账单核对，计划明确price_verified=false、invoice_cost_cap=null。这些是请求和token预留上限，不声称真实账单金额已锁定；按供应商usage另记已知实际tokens，缺失或部分usage保持unknown，不视作免费。

## 范围和结果口径

使用v2已见助手裁决材料、临时/独立SQL、生产KnowledgeBaseService的BM25冷检索；生成取实际排名前5条证据，不按金标挑证据。真实Embedding、Milvus、重排/改写消融、完整Agent/REST/WS和生产部署均不在本执行器的保证内。

实际生成复用ResponseNode._generate_response_with_llm和LLMService；独立审查复用LLMRAGJudge，实际判决回放validate_candidate，回放不再调用模型。每例最多一次生成和一次Judge；固定候选的Judge单独记录，用于比较模型审查与确定性/最终候选闸门，不能把“模型声称通过”当独立事实标签。

未执行任何业务工具，business_actions_executed=0为执行器结构观察。授权边界题只能核对模型文本行为，不能据此宣称完整Agent授权机制已验收。模型输出保存脱敏正文及原正文hash；脱敏改变正文时不把改后文字当原模型稿补验。response_behavior、covered_conditions和claim_support保留未标注，不从题目预期值反填；所有输出仍需逐例审阅，助手审阅也不升级独立人工金标。

所有已见样本仅作诊断，不能重称为未见留出。即使将来所有HTTP完成，结果也只为LIVE_DIAGNOSTIC_OUTPUT_REVIEW_REQUIRED，不自动把M4/M5改PASS。

## 派发保护与恢复

- 新额度只从固定`reports/m5-live/authorization.json`读取，必须绑定计划ID、用户明确声明、日期与有限上限；目前该文件不存在。原M4账本只核对hash及660/660，不改写。
- 新HTTP账本固定为`reports/m5-live/dispatch.sqlite`，与创建标记同时校验。两者缺一、计划/额度变化、未知操作均拒绝重建；不支持用报告目录或CLI参数增加额度。
- SDK的异步HTTP出口只允许预定HTTPS供应商、模型、POST chat/completions，关闭自动重试和重定向。完整请求在SQLite BEGIN IMMEDIATE事务中预留后才发出；操作ID唯一，跨线程并发也不能多占额度。供应商失败、超时或回包未知都保留派发记录。
- 固定OS排他锁保护真实执行；真实输出固定`reports/m5-live/current/`。恢复沿用SQL证据映射、原输出和派发记录；已发但未保存正文标unknown，不重发。模型/配置/源码/数据改变需重新审阅方案，不能悄悄沿用授权。
- 只保留主供应商，备用/本地模型、遥测、用户记忆和业务缓存关闭，SQL网关使用独立SQLite记录可信合成主体及usage。工作进程不读取业务连接地址，不迁移业务库或启停服务。

## 已完成验证

首次相关回归`reports/offline/20261105-133700-614261`为78通过、1失败：Windows上SQLite connection上下文只提交事务而未关闭文件，导致临时库删除保护用例在删除之前报WinError32。已将所有账本连接改为closing+事务上下文，不改保护断言；原失败报告保留。

修复后`python scripts/test_offline.py -q tests/test_m5_live_budget.py tests/test_m5_offline_chain.py tests/test_m5_metrics.py tests/test_llm_provider_config.py tests/test_m4_gateway.py tests/test_m5_judge.py --tb=short`：79通过、0失败/错误/跳过，12.67秒、169条既有弃用警告；报告`reports/offline/20261105-133749-189613`。覆盖显式授权、非法/扩大上限、请求/输入/输出硬限制、未知派发重启不重放、账本/标记丢失关闭、并发预留、错误URL/模型零派发和缺失usage不退预留。

完整SDK替身演练：`python scripts/evaluate_m5_live.py --dry-run`，报告`reports/m5/live-rehearsal-20261105-133729-545712/report.json`。616题全部走完，411次生成、616次Judge、1027次HTTP替身派发；HTTP输入预留9790143字节、输出预留368128。所有provider usage均为替身固定10/10，不能当实际模型tokens或质量统计。真正socket连接/DNS/sendto由审计钩子禁止，SDK只走MockTransport；external_model_calls=0。原始SQL与预算文件在演练临时目录，业务数据不变。

演练与79项回归源码指纹`b823e5b8db91f425600801164ce494355cce5f2f31cfdee76dca7d3592969702`，起止稳定完整。编译和切片Ruff F401/F821通过。仅新增评测脚本/账本/测试，无生产代码改变，本片不重复全量；前片1314全量属于之前源码版本。

## 真实评测尚未启动的原因

项目AGENTS.md明确规定：**“新增批量付费调用需新授权”**，原额度已全部使用。本轮泛化的继续执行指令未指定新增调用/Token额度，因此没有代填用户授权文件或调用供应商。

准备完成后可确认本方案的最多1027次、输入预留16000000、输出368128额度；授权后再依上述顺序执行。费用未知项、失败和未完成层次仍必须如实保留。
