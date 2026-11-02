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
