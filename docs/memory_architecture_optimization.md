# 多轮对话记忆架构优化建议报告

> 生成时间：2026-09-07
> 基于代码：`app/agent/memory.py`、`app/agent/state.py`、`app/services/chat_service.py`、`app/agent/graph.py`

---

## 一、当前架构概述

项目采用**三层记忆架构**：

| 层级 | 存储介质 | 生命周期 | 核心内容 |
|------|----------|----------|----------|
| 运行时 | `AgentState`（内存） | 单次请求 | messages、槽位、意图、工具调用、trace |
| 热缓存 | Redis `session:{id}`（TTL 24h） | 24 小时 | 完整 AgentState JSON 快照 |
| 长期 | SQLite/MySQL `messages` 表 | 永久 | 用户/助手消息、token、trace |

**核心流程**：

1. 用户发消息 → 路由层写入 `messages` 表 → `ChatService.hydrate_state` 从 Redis 恢复历史 AgentState → 追加当前消息 → 交给 Agent 状态机
2. Agent 执行完毕 → `SessionManager.save_state` 回写 Redis → 助手回复写入 `messages` 表
3. 消息超过 40 条 → 触发 `_compress_context`：保留最近 10 条 + 规则摘要

---

## 二、当前架构问题分析

### 问题 1：摘要生成基于规则，语义信息丢失严重

**现状**：`_generate_summary` ([memory.py:185-220](file:///app/agent/memory.py#L185-L220)) 仅做关键词提取和截断，无法理解对话语义。

**影响**：
- 复杂多轮对话（如退款协商）的上下文在压缩后基本丢失
- 摘要只有"用户询问: xxx"和"涉及实体: ORD123"，缺少对话结论
- 压缩后 Agent 无法理解之前讨论的来龙去脉

### 问题 2：压缩触发条件过于粗糙

**现状**：固定 `MAX_MESSAGES_BEFORE_COMPRESSION = 40` 条触发。

**影响**：
- 40 条短消息（如"好的""嗯"）实际 token 很少，压缩反而损失信息
- 40 条长回复（如政策文档全文）可能已超 LLM 上下文窗口
- 没有按 token 数动态判断

### 问题 3：Redis 与内存缓存无一致性保证

**现状**：`_state_cache` 字典和 Redis 各自读写，`save_state` 先写内存再写 Redis。

**影响**：
- 多进程部署时，进程 A 写入内存缓存，进程 B 从 Redis 读取到旧状态
- Redis 写入失败时只 log warning，内存缓存已更新但 Redis 仍是旧值
- 不会回滚内存缓存

### 问题 4：同一会话并发请求状态覆盖

**现状**：`hydrate_state` 读取 → 修改 → `save_state` 回写，无锁保护。

**影响**：
- 用户快速连发两条消息时，第二个请求可能读到第一个请求尚未保存的旧状态
- 两个请求各自追加消息后回写，后写入的覆盖前一个，丢失一条消息

### 问题 5：摘要对象仅存内存，Redis 重启后丢失

**现状**：`_summaries` 是进程内字典 ([memory.py:64](file:///app/agent/memory.py#L64))，`ConversationSummary` 对象不写入 Redis。

**影响**：
- Redis 重启或进程重启后，`get_summary` 返回 None（除非 state.conversation_summary 有文本）
- 摘要的 key_topics、entities、intent_history 结构化信息永久丢失

### 问题 6：数据库与 Redis 消息列表可能不一致

**现状**：路由层先写 `messages` 表，再走 Redis 状态恢复 + Agent 执行，最后写 Redis。如果中间出错，数据库有用户消息但 Redis 没有对应状态。

**影响**：
- 异常恢复后，Redis 中 messages 列表缺少某些消息，但数据库有
- `get_messages` 从 Redis 读取，返回不完整的历史
- `/history/{session_id}` API 从数据库读取，与 Agent 实际使用的上下文不一致

### 问题 7：意图历史未充分利用

**现状**：`ConversationSummary.intent_history` 记录了意图序列，但 Agent 节点中只使用 `state.detected_intent`（上一轮意图）。

**影响**：
- 无法识别意图切换模式（如"查单 → 退款 → 投诉"的升级路径）
- 无法基于意图序列预测下一步动作

### 问题 8：槽位信息在压缩后可能丢失

**现状**：压缩只处理 `state.messages`，`collected_info` 不受影响。但如果 `get_state` 从 Redis 反序列化时 `collected_info` 字段丢失（如序列化版本不一致），已收集的订单号等关键信息会丢失。

**影响**：
- 用户第三轮说"那就退吧"，Agent 不记得第一轮收集的订单号
- 需要重新追问，体验差

### 问题 9：消息时间戳精度不足

**现状**：`hydrate_state` 中用户消息时间戳为 `datetime.now().isoformat()`，秒级精度。

**影响**：
- 同一秒内的用户和助手消息排序不确定
- 数据库查询 `ORDER BY created_at` 可能返回错误顺序（已知问题）

---

## 三、优化建议

### 高优先级

#### 建议 1：引入 LLM 生成对话摘要

**当前**：规则摘要，只提取关键词和截断。

**建议**：在 `_compress_context` 触发时，调用 LLM 生成结构化摘要：

```python
# 伪代码
prompt = f"请将以下对话压缩为简洁摘要，保留关键实体、用户意图和结论：\n{older_messages}"
summary = await llm.generate(prompt, max_tokens=200)
```

**收益**：摘要质量大幅提升，Agent 压缩后仍能理解完整对话脉络。

**风险**：增加一次 LLM 调用延迟（约 500ms），仅在压缩时触发（每 40 条一次），影响可控。

#### 建议 2：基于 Token 数动态触发压缩

**当前**：固定 40 条消息触发。

**建议**：增加 token 估算，当 `estimated_tokens > 3000` 或 `messages > 20 `时触发，取先到者：

```python
def _estimate_tokens(messages):
    # 粗略估算：中文1字≈1.5 token，英文1词≈1 token
    total_chars = sum(len(m.get("content","")) for m in messages)
    return int(total_chars * 1.5)

if _estimate_tokens(state.messages) > 3000 or len(state.messages) > 40:
    await self._compress_context(state)
```

**收益**：长回复场景不会撑爆上下文窗口，短对话场景不浪费压缩。

#### 建议 3：增加分布式锁防止并发覆盖

**当前**：无锁，并发请求可能互相覆盖。

**建议**：在 `hydrate_state` + `save_state` 之间加 Redis 分布式锁：

```python
lock_key = f"session_lock:{session_id}"
lock = await redis.set(lock_key, "1", nx=True, ex=30)
if not lock:
    # 等待或排队
    await asyncio.sleep(0.5)
    # 重试获取...
try:
    state = await hydrate_state(...)
    result = await agent_graph.run(state)
    await save_state(result)
finally:
    await redis.delete(lock_key)
```

**收益**：同一会话的请求串行化，避免状态覆盖。

#### 建议 4：摘要对象持久化到 Redis

**当前**：`_summaries` 仅存内存。

**建议**：将 `ConversationSummary` 序列化后存入 Redis：

```python
await redis.setex(
    f"session_summary:{session_id}",
    SESSION_TTL_SECONDS,
    json.dumps(summary.to_dict())
)
```

`get_summary` 时优先从 Redis 读取。同时 `AgentState.conversation_summary` 已存文本摘要，可保留作为降级。

**收益**：进程重启后摘要不丢失，结构化信息（entities、intent_history）可跨进程访问。

#### 建议 5：数据库作为消息历史的最终真相源

**当前**：`get_messages` 从 Redis 读取，`/history` API 从数据库读取，两者可能不一致。

**建议**：`get_messages` 增加数据库回退逻辑：

```python
async def get_messages(self, session_id, limit=50):
    state = await self.get_state(session_id)
    if state and state.messages:
        return state.messages[-limit:]
    # Redis 未命中，从数据库回退
    async with async_session() as db:
        rows = await db.execute(
            select(Message).where(Message.session_id == session_id)
            .order_by(Message.created_at).limit(limit)
        )
        return [{"role": r.role, "content": r.content} for r in rows]
```

**收益**：Redis 故障时不丢失消息历史，保证最终一致性。

### 中优先级

#### 建议 6：增加意图序列分析

**当前**：只使用上一轮意图做延续性判断。

**建议**：在意图识别节点读取 `intent_history`，识别意图升级模式：

- 检测到"query_order → refund → complaint"序列时，自动提高投诉权重
- 检测到连续同一意图时，降低澄清阈值（用户可能在补充信息）

#### 建议 7：消息时间戳改用毫秒精度

**当前**：`datetime.now().isoformat()` 秒级精度。

**建议**：改为 `datetime.now().isoformat(timespec='milliseconds')`，数据库字段也同步调整。

**收益**：同一秒内的消息排序确定，已知问题得到解决。

#### 建议 8：压缩时保留关键槽位快照

**当前**：压缩只处理 messages。

**建议**：压缩时将 `collected_info` 的关键槽位（order_id、refund_reason 等）写入摘要的 entities 字段，作为冗余备份。反序列化时从摘要恢复。

### 低优先级

#### 建议 9：增加会话活跃度检测

**当前**：Redis TTL 固定 24h。

**建议**：根据最后活跃时间动态调整 TTL——活跃会话延长至 72h，非活跃缩短至 2h，释放资源。

#### 建议 10：增加记忆架构监控指标

**建议**：暴露以下 Prometheus 指标：

- `session_compression_total`：压缩触发次数
- `session_message_count`：各会话消息数分布
- `session_redis_fallback_total`：Redis 降级到内存次数
- `session_state_inconsistency_total`：数据库与 Redis 不一致次数

---

## 四、优先级与预估收益总结

| 优先级 | 建议 | 预估收益 |
|--------|------|----------|
| 高 | LLM 生成摘要 | 压缩后上下文质量提升 60%+ |
| 高 | Token 数动态压缩 | 避免长对话 OOM |
| 高 | 分布式锁 | 消除并发状态覆盖 |
| 高 | 摘要持久化 Redis | 进程重启不丢失摘要 |
| 高 | 数据库回退读消息 | 保证最终一致性 |
| 中 | 意图序列分析 | 提升多轮意图识别准确率 |
| 中 | 毫秒时间戳 | 解决已知消息排序 bug |
| 中 | 槽位快照备份 | 长对话槽位不丢失 |
| 低 | 动态 TTL | 优化 Redis 资源利用 |
| 低 | 监控指标 | 可观测性提升 |

---

## 五、架构演进方向

当前三层架构（AgentState → Redis → DB）是合理的，建议在此基础上：

1. **短期**（1-2 天）：修复时间戳、摘要持久化、数据库回退——改动小、风险低
2. **中期**（3-5 天）：引入 LLM 摘要、Token 动态压缩、分布式锁——改动中等、收益大
3. **长期**（1-2 周）：意图序列建模、会话活跃度管理、监控体系——需要数据和调优
