# 任务循环修正与复验 v3

最新修正见[反馈来源与完整复验v4](AGENT-TASK-LOOP-FEEDBACK-v4.md)：最终离线2233通过，同一冻结源码的完整12任务20轮真实复验全部通过，新授权使用61/120次，隔离MySQL/native HTTP部署回放通过；现有业务容器未替换。以下保留旧117次阶段及失败历史。

日期：2026-11-07。用户在首轮失败结果后要求“修正并复验”。本次继续使用原120次HTTP总上限，首轮49次保留，新增最多71次；不重置账本或覆盖首轮失败。

## 修正

- 任务模式的混合只读例外：仅`TaskRequestUnderstanding`、任务开关有效、multi任务、technical/refund咨询、同时明确包含本主题咨询和query_order/query子请求时，允许该已知订单的query_order计划。普通RequestUnderstanding、单一咨询、关闭任务模式、历史读取和未知/错配订单仍拒绝。所有实体来源及权限检查继续执行。
- 模型输出改为`TaskDecision.step`的按action判别联合类型。read、search_knowledge、ask_user、prepare_refund、finish、suggest_handoff各自只暴露适用字段，均禁止额外字段；成功解析后再构造既有`NextStep`并运行原跨字段检查。旧非法输出不会被静默丢字段后放行。
- 排障生成提示明确“检查过不代表结果正常”，未明确反馈的电源/指示灯/连接状态仍未知。此提示不是语义正确性的程序证明，须看真实输出复核。
- Compose传递`AGENT_TASK_REFUND_PREVIEW_ENABLED`，默认false；任务退款预览的独立开关不替代既有单一退款业务的确认/权限契约。

## 回归与冻结

- 首轮捕获的混合理解及9条非法决策保存到`acceptance/agent-task-loop/captured-failures-v1.json`，全为合成输入，无真实用户信息。捕获fixture只作为开发反例，不是新的独立金标。
- 定向142项通过：`reports/offline/20261107-033328-873189`。覆盖原捕获混合计划、普通咨询/关闭开关/未知订单/历史读取不能扩大权限、9条旧非法输出仍被拒绝、新模型联合类型及原图/确认/SQL持久化契约。
- 新SDK演练：`reports/task-loop/admission-v1/revalidation-v2/rehearsals/20261107-033426-275447/report.json`，12/12客观断言、20轮完成、真实调用0。
- 候选镜像`ai-customer-task-loop:revision2-20261107`，189份Python输入与工作区零差异；两项任务开关默认false；`docker compose config --quiet`通过。数据库模型和0012迁移本次未改变，沿用首轮的隔离MySQL增量/重复迁移证据，不将旧镜像核验当新镜像核验。
- 编译、相关文件Ruff F401/F821、`git diff --check`通过。源码指纹`f5361fdeef8b864056081da609604a8805afb0b090d29eadf30fdd64fc976ea4`。
- 冻结全量：`reports/offline/20261107-033507-894572`，2193通过，0失败/错误/跳过，374.40秒；12772条既有警告。pytest/证据退出码0，来源稳定完整，blocked_network_operations=0。真实派发在该结果确认后开始。

## 预算及证据连续性

复验复用`reports/task-loop/admission-v1/dispatch.sqlite`及其原metadata/创建标记，保留原prepared身份和120次总限。新操作使用`revision-v2/`前缀；账本同时识别原操作和新操作，但SQL原子预留始终对全部操作累计计数。新增回归验证旧行保留、标记不变以及不同前缀不能绕过总上限。

新版冻结额外绑定首轮所有JSON原始报告/输出的hash和49条旧派发行的规范hash；新行追加不会改变旧行。M4/M5及请求理解旧账本继续受保护。复验结果写入固定`revalidation-v2/current/`，首轮`current/`原文件不改。

复验仍为相同12任务20轮、隔离ASGI/SQLite/合成BM25知识和真实模型；不包含业务向量模型、真实Redis/MySQL并发、浏览器或生产SLO。真实结果、完整输出复核及最终启用决定追加于下方。

## 第二轮完整实测

`reports/task-loop/admission-v1/revalidation-v2/current/report.json`：12任务20轮完整执行，客观断言11/12；新增60次HTTP，连首轮累计109/120，usage未知0，来源起止稳定完整。P50 3996.231ms、P95 7798.497ms、最大9666.129ms，仍是隔离顺序链路且含失败，不能当生产SLO通过。

混合咨询+订单查询、三个模拟售后确认/取消/条件变化场景均推进，原两类契约错误已消除；捕获22次TaskDecision均通过结构校验。唯一客观失败是diagnostic-01第二轮：初始检索已完成，模型再次选择完全相同的troubleshooting检索，重复调用保护停止了任务。原始失败保留，不把11/12写成完整通过。

逐条助手复核另发现diagnostic-04第二轮“电源这一步已排除”比用户的“检查之后仍然断线”更强；conditional-03正确阻止提交，但公开说明降级为通用审查失败。详见同目录`manual-review.json`。因此结构及程序断言不等于通用语义质量通过。

## 重复检索专项收口

仅对troubleshoot任务：模型再次提出本轮同参数、已有证据的检索时，保留blocked审计，不重复执行检索，转到现有ask_user/diagnostic_feedback及生成/Judge/发布链。没有当前证据时仍停止；售后重复检索仍停止；不得借此准备业务动作或直接发布草稿。该收尾不额外请求规划模型。

- 定向133通过：`reports/offline/20261107-034817-664126`，包含当前证据存在/失效、售后重复仍拦截、补问草稿审查未知仍停止。
- 专项SDK演练：`reports/task-loop/admission-v1/revalidation-v3/rehearsals/20261107-035044-158175/report.json`，1任务2轮通过，第二轮由SDK替身明确提出重复检索以覆盖新分支；真实调用0。
- 固定专项只重验原diagnostic-01的2轮，不新增材料、不改标签；继续原账本，追加前累计109，新增最多11。冻结原两轮全部JSON和109条旧派发行；新操作前缀revision-v3。此专项不能被写成最终源码的一次完整12任务新实测。
- 最终冻结全量：`reports/offline/20261107-035135-613439`，2198通过，0失败/错误/跳过，319.55秒，12772条既有警告；pytest/证据退出码0，来源稳定完整，blocked_network_operations=0。最终来源`bc3bb19db4980bf74c8fc09f1937f6a34306d2ab25ec887c634ed4e9fbf9a660`。
- 专项真实结果：`reports/task-loop/admission-v1/revalidation-v3/current/report.json`，1/1任务、2轮通过，新增8次HTTP，来源起止稳定。真实SQL审计观察到第二轮search_knowledge:troubleshooting被标blocked/duplicate_search_feedback，未再次检索，随后通过生成与Judge审查进入waiting_input；两轮任务ID一致。
- 专项回复保留“检查电源后指示灯仍不亮”的用户事实，没有声称电源已排除；原判断和补问保持独立审查。助手复核记录同目录`manual-review.json`，不是独立人工质量金标。
- 新镜像`ai-customer-task-loop:revision3-20261107`与工作区189份Python输入零差异，两项任务开关默认关闭；未替换业务容器。

## 最终结论与范围

两处原始契约问题已修正并得到真实复验；新增重复检索收尾也完成了专项真实验证。完整实测的11/12和最终补丁的1/1是不同冻结源码版本，不能拼接为最终版本一次完整12/12新实测。第二轮逐条复核的“电源已排除”等语义问题仍须进一步评测，不将结构合法、程序断言或助手复核当作一般语义可靠性证明。

累计HTTP **117/120**（首轮49、完整复验60、专项8），剩余3次不自动追加或复跑。已知实际输入276482、输出24216 tokens，usage未知0；未对供应商账单核验。117条派发保留在同一账本；首轮49和第二轮60行、两轮全部原报告/输出及旧M4/M5/请求理解账本指纹均保持。原ledger metadata/创建标记未改，未另建付费账本。

本次不启用业务任务循环，业务.env、运行镜像、业务数据库迁移状态均未改变。原因是最终版本尚无一次全套新真实验收，且逐条复核仍有语义限制；剩余3次不足以完成全套模型链路。机器收口：`reports/task-loop/closeout-v3-20261107.json`。最终修正源码、候选镜像和全部失败/通过记录保留。
