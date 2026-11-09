# 0013、API与后台Worker部署验收

日期：2026-11-07。**本机部署已完成，九个服务健康。** 范围：部署现有Agent可靠性实现、迁移0013、补齐常驻业务Worker，并在与部署一致的Python依赖中验证。不更换聊天/向量/重排模型，不启用Judge v2或实验推理路径。

## 本轮修正

- MCP使用FastMCP公开的Client内存传输及原始协议结果接口，移除对已删除SDK内存连接助手和私有server属性的依赖。兼容协议错误字段is_error/isError；错误写回执仍不能通过直接工具重做。mcp==2.2.0、fastmcp==4.0.3同时进入requirements与约束文件。
- MCP依赖在FastAPI启动阶段预载，再执行原有2秒连接/list_tools检查；不为冷导入放宽每次工具/父Run截止。失败仍明确降级到进程内工具；健康接口增加启动MCP状态。
- 0013在MySQL上按已存在users/sessions/chat_runs实际主键类型及排序规则创建外键列，不依赖目标数据库的默认排序规则。迁移不修改旧表或删除数据，重复执行可用。
- Compose默认增加worker，使用同一业务镜像、配置与SQL数据库，不发布端口；消费摘要、通知Outbox、知识索引并恢复过期Run。未配置渠道时通知明确失败，不使用模拟发送冒充外部投递。
- Worker启动检查0013及执行账本表；健康检查要求近期成功周期、当前schema和当前Linux进程启动标识，防止重启复用PID时继承旧心跳。SIGTERM停止继续领取任务，完成当前处理后关闭连接；Compose启用init和130秒停止宽限。
- 本机API与Worker共享Redis本地模型准入及相同namespace，保留GPU原模型/维度/精度；不将此互斥验证当作吞吐或生产容量验收。
- start.ps1调整为构建镜像、启动SQL/Redis/Milvus依赖、迁移、再启动API与Worker，避免新Worker先于迁移消费。
- 五个旧MCP协议回归改用当前连接入口，保留原退款、工单、通知和转人工持久化断言。

## 部署与回退

私有配置、在线一致性SQL备份及切换前静默SQL备份保存在忽略目录reports/reliability-rollout/private，不加入版本控制。在线备份恢复到无宿主机端口的独立MySQL，验证0012到0013增量及重复迁移、旧数据计数与发布知识保持。

旧镜像ai-customer:before-reliability-20261107保留。回退应用时停止对应Worker，保留0013和全部执行记录，不执行有损downgrade；新任务JSON包含扩展字段，旧镜像不保证承接这些新任务。数据库恢复不能自动覆盖升级后新增业务数据。

后续常规启动为docker compose up -d，API与worker共用同一镜像；本机LOCAL_MODEL_COORDINATOR必须保持redis。查看docker compose ps以及worker日志可检查运行情况。修改依赖后需重新执行容器回归，不能用宿主机不同版本的通过结果代替。

## 验证过程

隔离MySQL首次验证暴露默认排序规则与旧表外键不兼容，修正后通过。MCP冷导入偶发超过连接截止，改为启动预载；真实协议查询合成订单和SQL执行账本关联已通过。独立MySQL并发派发相同能力仅一个成功登记，另一个被阻止；跨容器Redis合成探测验证互斥及释放后重新准入，未调用GPU模型。

隔离Worker已消费原备份中的17条摘要任务。重启测试首次暴露旧心跳/PID复用问题，修正后健康、正常停止exit0、重启健康均通过。容器全量初次在五个旧SDK测试中的首项遇到ImportError，保留诊断输出并中断，更新所有旧入口后重新冻结源码验证，不将中断运行算作全量通过。

容器验证镜像只增加Git和SQLite测试依赖；正式业务镜像不加入这些测试工具。全量使用复制到Linux的冻结源码及原Git元数据，network=none并保留离线审计，避免Windows绑定目录的I/O干扰。源码指纹在执行前后及部署前核对。

## 最终收口

- 当前API/Worker同一镜像：`sha256:831db2004376de527b96a69e2f8dfcab76faf806e51f241adbbbb33a347c03fd`，标签`ai-customer:latest`和`ai-customer:reliability-0013-20261107`。193份app/scripts/alembic Python文件与工作区完全匹配；API已恢复标准Uvicorn命令，API与Worker均没有临时验证挂载。
- 业务库实查0013，能力账本表及7条本次合成执行记录均存在、状态succeeded，无running遗留。三个任务分别completed/waiting_input/cancelled，持久化6条有来源任务记录。24份发布文档、1067个当前发布片段及原Embedding指纹保持；原始SQL版本和旧collection不删除。
- API、worker、MySQL、Redis、etcd、MinIO、Milvus、Embedding和Reranker全部healthy。原17条摘要队列及本次6轮产生的摘要任务共23条均已消费；既有1条失败索引Job原样保留，未将它冒称本次成功。
- 正式Python依赖与验证镜像逐项一致；验证镜像仅额外安装aiosqlite。`pip check`、Python编译、改动核心代码Ruff F401/F821、Compose配置及PowerShell启动脚本语法检查通过。五个兼容测试文件已有pytest fixture导入等F401存量，不以删除fixture的方式处理。

### 容器回归证据

单进程完整诊断`reports/offline/20261107-052301-627813`为2270通过/3失败：通知协议测试仍按旧SDK读取`listed.tools`，未执行到发送断言即失败。修正为当前公开列表返回值，保留通知不可假称投递及数据库不访问断言；三项定向`20261107-054004-688740`通过。

最终同一冻结源码四分片覆盖全部**2273项**，初始2272通过/1失败：

- `20261107-053702-985474`：503通过/1个实验batch公平性测试失败。
- `20261107-053702-983845`：323通过。
- `20261107-053702-984012`：614通过。
- `20261107-053702-998579`：832通过。

唯一失败为`test_background_gets_next_slot_after_four_interactive_admissions`：测试等待两个队列项同时可见，但入队没有给观察者发通知，受并行调度影响可错过50ms后台等待窗口。未改业务实现、期限或断言；原测试单独复验`20261107-054222-636640`在0.13秒通过。当前生产配置使用redis，不启用该batch实验模式。这是全量分片加一次串行时序复验，**不是单次零失败的完整运行或并发性能验收**。

四分片及复验均有独立临时SQL、网络审计与完整稳定源码记录；blocked_network_operations=0，无跳过。测试文件分区完整、用例身份无重复，来源指纹`29da93f4c503f297e5318a134501f85bad6bbe115e13b706e0814a231de1b80e`。汇总见`reports/reliability-rollout/full-validation.json`，失败原件不覆盖。

### 真实运行与额度

新服务真实REST五轮通过：订单+知识、多轮排障、条件模拟退款预览和取消；重放相同client_message_id结果一致。原生认证WebSocket问候通过且不调用供应商。6个Run、3个任务，退款申请0；合成账号禁用并确认旧Token401。没有真实支付或外部通知。

Redis准入下真实检索backend=milvus、reranker=local、degraded=false；该单次探测与合成跨容器互斥不能外推生产吞吐/P95。真实MCP/SQL工具及并发派发去重已在独立恢复MySQL验证；stdio、长期稳定性、独立人工语义金标仍未验收。

同一v4原账本本次新增**18次**供应商HTTP，已知输入48724、输出3950 tokens，usage未知0；阶段累计**111/120，剩余9**。原93行逐列保持，另外13份旧派发账本hash保持。没有新建账本绕过上限，费用未与供应商账单核对，剩余额度不自动用于新批量任务。

机器收口：`reports/reliability-rollout/closeout.json`；辅助证据包括`business-final.json`、`budget-closeout.json`、`dependency-comparison.json`、`migration-from-restored-0012.json`、`worker-lifecycle.json`和`final-health.json`。保留本轮原始失败、私有备份及旧镜像；未提交整个已有脏工作区。
