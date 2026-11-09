# LLM 治理最小基线

项目中所有外部 LLM 调用应经过 `app.services.llm_governance` 中的 `ArtifactRegistry`、`BudgetManager` 和 `SensitiveDataRedactor`，以便在不记录用户原文的前提下复现问题。

当前基线通过 `build_default_registry` 管理四类版本：Prompt、Model、结构化 Schema 和路由策略，支持 `activate` 灰度切换及快照回滚。模型供应商未返回账单时，`CostEstimator` 使用实际输入/输出 Token 和配置的每千 Token 单价计算估算费用，并明确这是估算值。

发送到外部模型前调用 `SensitiveDataRedactor.redact`/`sanitize_messages`，会移除密码、Bearer/JWT Token、手机号、邮箱和带标签的完整地址。`PromptInjectionScanner` 检测到“忽略之前指令”、系统 Prompt 泄露和越权角色等请求时，应直接走规则或 Mock 降级，不执行工具副作用。

`BudgetManager.check`/`acheck` 同时检查单次 Token/费用预算和用户当日累计预算；不允许的决策包含明确原因。调用成功后用 `consume`/`aconsume` 累计 Token 和估算费用；不可用、注入或超预算时由 LLMService 记录可解释的降级 route。预算存储为 Redis 优先（hash key `budget:{user}:{day}`，多 worker 共享日累计），Redis 不可用时自动降级进程内 dict（单 worker 语义不变）。

回归测试：

```powershell
pytest tests/test_llm_governance.py
```

实现入口为 `app.services.llm_governance`：`ArtifactRegistry` 管理 prompt/model/schema/route
版本并支持激活与快照，`BudgetManager` 执行单次及用户日 token/费用预算，
`SensitiveDataRedactor` 和 `PromptInjectionScanner` 在外部模型边界执行数据保护，
`ToolPolicy` 只允许白名单工具。`LLMService.get_last_call_metadata()` 返回不含原文的
版本、路由、token、估算费用、耗时和降级原因；规则、Mock、外部供应商路径均使用同一格式。
预算单价来自 `LLM_INPUT_PRICE_PER_1K_USD`/`LLM_OUTPUT_PRICE_PER_1K_USD`，成本明确标记为
`configured_token_rates_estimate`，不冒充供应商账单。生产部署继续由代码执行 RBAC、
资源归属、订单/退款资格、状态流转和最终副作用确认。

## M4 SQL网关候选（2026-11-04）

显式启用网关时，普通/结构化/流式每次实际尝试先预留SQL日tokens/估算费用、共享并发槽，Run最多6尝试且共享费用/deadline。完整消息和schema以保守字节上界预估输入；真实usage结算，缺失/部分usage保留预留，失联或worker中断标unknown。价格快照/模型revision保留，管理员可按用途核对。旧Redis预算不再重复扣费；无Run的兼容调用归system user0，不能声称终端用户归属完整。SQL槽按同数据库实例协调，不覆盖未启用网关的旧进程。

当前业务rules，model实体质量门槛失败；公开价格为基础tier估算，账户倍率/实际账单未验收。656次真实合成请求结果及未验范围见M4-EVIDENCE-v1.md，不能用旧商品变体矩阵代替300条独立语义留出。

M4故障补验：同执行上下文并行预留共享锁，跨实例仍依赖SQL日预算/并发槽与Run租约；发出前在SQL等待和本地槽等待之后检查deadline。确知未发出调用标not_sent并退还SQL预留；已发出未知保持保守预留。费率及价格验证标签使用调用快照，不随热配置变化。SDK+MockTransport故障矩阵不是真实供应商故障实测。

当前候选身份补齐：独立图/聊天（含整个供应商回退链）/流式/RAG问答/兼容摘要通过可信应用入口建立实际用户唯一scope，正式Run继承原Run/factory。结构化调用无上下文时不再默认为user0，而是拒绝请求；不能从模型输出建立主体。RAG使用认证用户且临时客户端总是关闭，兼容摘要修复原错误单例导入并关闭实例。主栈后台rules-v1摘要仍无模型费用；历史user0账本保留原状。

模型SDK客户端现在显式使用服务自有sync/async httpx池（每池连接数受LLM_MAX_CONCURRENCY约束），不共享库默认base/timeout全局缓存，临时服务关闭不会关闭聊天图的底层池。close尝试所有去重客户端并清缓存，失败不跳过其他池。FastAPI退出finally清理健康/聊天图各实例及MCP/Redis。260次本机合成协议请求验证资源归零，不能替代多小时真实供应商压力验收。

生成节点与持久评测/Trace使用严格GenerationMetadata观察头，服务last_call_metadata中的structured_output/输入摘要/额外字段不复制到状态。partial/unknown usage明确区分，缺失output不按0计算实际费用；未知头不缓存回答，SQL ModelCallRecord保持计费事实。该头和LLMService展示tokens估算均不替代供应商账单。

Graph模型服务被显式替换时，共享原服务的改写/Judge组件同步重绑定，避免只替换生成/意图而遗留旧请求适配器；专用独立模型不被覆盖。测试捕获与费用授权仍应覆盖所有显式供应商实例，SQL Run预算继续统一约束，旧客户端由调用方关闭。

## Judge v2候选与费用边界

LLM_JUDGE_CONTRACT_VERSION=v1|v2默认v1；v2拆分answerability/response_validity/response_behavior，由服务器核验句子覆盖、当前输入hash及证据ID/版本/quote，最终程序决定publish_allowed。原模型总分不能覆盖明确的范围/否定/条件错误。partial/unknown的合适补问/拒答不视为错误候选，但不进回答缓存。临时原文证明不复制进审查状态，旧契约不能冒充v2。v2单次输出最多min(全局配置,1024)，未做新真实供应商验证；原M4 660和M5 1027调用额度均已耗尽，本片新增0次。详情及未验语义识别见M5-JUDGE-CONTRACT-v2.md。

2026-11-07：回答组件采用确定性检查＋独立Judge＋程序发布决策，Judge失败为审查unknown，与资料不足但补问正确分开。只有明确rejected的模型草稿可修复一次并复审，unknown不重生成，失败不自动转人工；修复原文只在ContextVar临时传递。v2提示新增条件/子问题覆盖及不必要补问检查，契约修订candidate.2；本轮不切业务配置、不新增真实调用，模型语义质量仍未验收。缓存validated-v4隔离旧策略，命中复审；发布前核对输入与策略指纹和知识发布快照。实现与离线范围见[ANSWER-PIPELINE-v1.md](ANSWER-PIPELINE-v1.md)。

## Judge v2 JSON协议候选补充（2026-11-08）

`LLM_JUDGE_V2_OUTPUT_PROTOCOL`默认function_calling；候选可显式使用json_object，仅改变v2独立Judge，不改变v1/理解/生成。json_object请求指定response_format和完整schema，标准json.loads严格验证，拒绝重复键/非有限数字/Markdown/截断/工具回包，无自动修补、重试或失败协议回退；协议纳入回答策略绑定。服务端片段ID改用无数字可逆编码以避免手机号脱敏误伤，真实隐私脱敏保持。此候选未部署，完整真实语义准入仍失败，最新预算21/80及范围见[JUDGE-V2-JSON-PROTOCOL-FIX-v1.md](JUDGE-V2-JSON-PROTOCOL-FIX-v1.md)。
